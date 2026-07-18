"""
Unit tests for get_safe_path() — the security-critical path validation function.

These tests verify that get_safe_path() correctly:
  - Rejects directory traversal attacks (../../etc/passwd)
  - Rejects tilde (~) expansion that escapes the workspace
  - Rejects symlink escapes
  - Accepts valid relative paths, nested subdirectories, root boundary paths
  - Handles non-existent paths correctly
  - Produces correct error messages

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import get_safe_path
import minutus.minutus as minutus_mod

# Register the 'unit' marker so pytest doesn't warn about unknown markers.
# This allows running only unit tests (no API key needed) via:
#   pytest -m unit
pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper to assert ValueError with "Security Error" prefix
# ---------------------------------------------------------------------------

def assert_security_error(exc_info, original_path=None):
    """Assert that a ValueError is a security error from get_safe_path()."""
    assert "Security Error" in str(exc_info.value)
    if original_path is not None:
        assert original_path in str(exc_info.value)


# ---------------------------------------------------------------------------
# 3.1 — Directory Traversal Attacks (must reject)
# ---------------------------------------------------------------------------

class TestDirectoryTraversal:
    """Tests that directory traversal attacks are rejected."""

    def test_traversal_above_root(self, trusted_root):
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("../../etc/passwd")
        assert_security_error(exc_info, "../../etc/passwd")

    def test_traversal_to_absolute_path_outside(self, trusted_root):
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("/etc/passwd")
        assert_security_error(exc_info, "/etc/passwd")

    def test_traversal_nested_dotdot(self, trusted_root):
        # Create the subdir so resolve() has something to anchor on,
        # but the .. chain still escapes above root.
        (trusted_root / "subdir").mkdir()
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("subdir/../../..")
        assert_security_error(exc_info)

    def test_traversal_with_valid_prefix(self, trusted_root):
        # A valid directory name at the front doesn't help — the .. cancels it.
        (trusted_root / "valid_dir").mkdir()
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("valid_dir/../../../etc/passwd")
        assert_security_error(exc_info)


# ---------------------------------------------------------------------------
# 3.2 — Tilde / Home Expansion (must reject)
# ---------------------------------------------------------------------------

class TestTildeExpansion:
    """Tests that ~ expansion (which resolves to the real home dir) is rejected."""

    def test_tilde_expansion_rejected(self, trusted_root):
        # ~ expands to the real user home, which is outside tmp_path.
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("~")
        assert_security_error(exc_info, "~")

    def test_tilde_with_path_rejected(self, trusted_root):
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("~/somefile.txt")
        assert_security_error(exc_info, "~/somefile.txt")


# ---------------------------------------------------------------------------
# 3.3 — Symlink Escapes (must reject)
# ---------------------------------------------------------------------------

# Skip all symlink tests on platforms that don't support os.symlink
symlink_support = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt",
    reason="Symlink support is required for these tests",
)


@symlink_support
class TestSymlinkEscapes:
    """Tests that symlinks pointing outside the trusted root are rejected."""

    def test_symlink_escape_to_outside(self, trusted_root, tmp_path):
        # Create a directory outside the trusted root (but still in tmp_path's parent)
        outside_dir = tmp_path.parent / "outside_target"
        outside_dir.mkdir(exist_ok=True)

        # Create a symlink inside trusted_root that points outside
        link_path = trusted_root / "escape_link"
        os.symlink(outside_dir, link_path)

        with pytest.raises(ValueError) as exc_info:
            get_safe_path("escape_link")
        assert_security_error(exc_info)

    def test_symlink_chain_escape(self, trusted_root, tmp_path):
        # Create a directory outside the trusted root
        outside_dir = tmp_path.parent / "chain_target"
        outside_dir.mkdir(exist_ok=True)

        # Create symlink B inside trusted_root pointing outside
        link_b = trusted_root / "link_b"
        os.symlink(outside_dir, link_b)

        # Create symlink A inside trusted_root pointing to B
        link_a = trusted_root / "link_a"
        os.symlink(link_b, link_a)

        with pytest.raises(ValueError) as exc_info:
            get_safe_path("link_a")
        assert_security_error(exc_info)


# ---------------------------------------------------------------------------
# 3.4 — Valid Paths (must accept)
# ---------------------------------------------------------------------------

class TestValidPaths:
    """Tests that legitimate paths within the trusted root are accepted."""

    def test_valid_relative_path(self, trusted_root):
        result = get_safe_path("file.txt")
        assert result == (trusted_root / "file.txt").resolve()
        assert result.is_relative_to(trusted_root)

    def test_valid_nested_subdirectory(self, trusted_root):
        # Create the subdirectories so resolve() can anchor on them
        (trusted_root / "sub1" / "sub2").mkdir(parents=True)
        result = get_safe_path("sub1/sub2/file.txt")
        assert result == (trusted_root / "sub1" / "sub2" / "file.txt").resolve()
        assert result.is_relative_to(trusted_root)

    def test_dot_current_dir(self, trusted_root):
        result = get_safe_path(".")
        assert result == trusted_root

    def test_dot_dot_within_root(self, trusted_root):
        # Create subdir so subdir/.. resolves back to root
        (trusted_root / "subdir").mkdir()
        result = get_safe_path("subdir/..")
        assert result == trusted_root

    def test_root_itself(self, trusted_root):
        # Passing "." should return the root itself
        result = get_safe_path(".")
        assert result == trusted_root


# ---------------------------------------------------------------------------
# 3.5 — Non-existent Paths (behavior check)
# ---------------------------------------------------------------------------

class TestNonExistentPaths:
    """Tests for paths that don't exist on disk — resolve() still normalizes them."""

    def test_nonexistent_path_within_root(self, trusted_root):
        # Path doesn't exist but stays within root after resolution
        result = get_safe_path("does_not_exist.txt")
        assert result == (trusted_root / "does_not_exist.txt").resolve()
        assert result.is_relative_to(trusted_root)

    def test_nonexistent_path_outside_root(self, trusted_root):
        # Path doesn't exist AND resolves outside root — must be rejected
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("../../nonexistent")
        assert_security_error(exc_info)


# ---------------------------------------------------------------------------
# 3.6 — Edge Cases at the Root Boundary
# ---------------------------------------------------------------------------

class TestRootBoundary:
    """Tests for paths exactly at or just outside the root boundary."""

    def test_path_exactly_at_root(self, trusted_root):
        # The absolute path to the root itself should be accepted
        result = get_safe_path(str(trusted_root))
        assert result == trusted_root

    def test_path_parent_of_root(self, trusted_root):
        # One level above root should be rejected
        with pytest.raises(ValueError) as exc_info:
            get_safe_path(str(trusted_root.parent))
        assert_security_error(exc_info)

    def test_double_dot_at_root(self, trusted_root):
        # ".." from the root resolves to the parent of root — must be rejected
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("..")
        assert_security_error(exc_info)


# ---------------------------------------------------------------------------
# 3.7 — Error Message Content
# ---------------------------------------------------------------------------

class TestErrorMessages:
    """Tests that error messages contain the expected information."""

    def test_error_message_contains_original_path(self, trusted_root):
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("../../etc/passwd")
        assert "../../etc/passwd" in str(exc_info.value)

    def test_error_message_contains_security_prefix(self, trusted_root):
        with pytest.raises(ValueError) as exc_info:
            get_safe_path("/etc/passwd")
        assert str(exc_info.value).startswith("Security Error:")
