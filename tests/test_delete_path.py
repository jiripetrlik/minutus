"""
Unit tests for the delete_path @tool function.

Tests cover:
  - Deleting single files (in root and in a subdirectory)
  - Deleting single empty directories
  - Refusing directories that contain anything (files, subdirs, ignored entries)
  - Refusing the workspace root
  - Security: path escape and symlink escape rejection
  - Missing paths
  - Symlink-to-in-workspace-target semantics

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import delete_path

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Deleting files
# ---------------------------------------------------------------------------

class TestDeleteFile:
    """Tests for deleting single files."""

    def test_delete_file_in_root(self, trusted_root):
        f = trusted_root / "gone.txt"
        f.write_text("data")
        result = delete_path.invoke({"path": "gone.txt"})
        assert result == "Successfully deleted file gone.txt."
        assert not f.exists()

    def test_delete_file_in_subdirectory(self, trusted_root):
        d = trusted_root / "sub"
        d.mkdir()
        f = d / "gone.txt"
        f.write_text("data")
        result = delete_path.invoke({"path": "sub/gone.txt"})
        assert "Successfully deleted file" in result
        assert not f.exists()
        assert d.exists()

    def test_delete_empty_file(self, trusted_root):
        f = trusted_root / "empty.txt"
        f.write_text("")
        result = delete_path.invoke({"path": "empty.txt"})
        assert "Successfully deleted file" in result
        assert not f.exists()


# ---------------------------------------------------------------------------
# Deleting empty directories
# ---------------------------------------------------------------------------

class TestDeleteEmptyDirectory:
    """Tests for deleting single empty directories."""

    def test_delete_empty_directory(self, trusted_root):
        d = trusted_root / "emptydir"
        d.mkdir()
        result = delete_path.invoke({"path": "emptydir"})
        assert result == "Successfully deleted empty directory emptydir."
        assert not d.exists()

    def test_delete_empty_nested_directory(self, trusted_root):
        parent = trusted_root / "parent"
        child = parent / "child"
        child.mkdir(parents=True)
        result = delete_path.invoke({"path": "parent/child"})
        assert "Successfully deleted empty directory" in result
        assert not child.exists()
        assert parent.exists()


# ---------------------------------------------------------------------------
# Refusing non-empty directories
# ---------------------------------------------------------------------------

class TestNonEmptyDirectory:
    """Tests that directories with any content are refused and left intact."""

    def test_directory_with_file_refused(self, trusted_root):
        d = trusted_root / "nonempty"
        d.mkdir()
        (d / "keep.txt").write_text("data")
        result = delete_path.invoke({"path": "nonempty"})
        assert "is not empty" in result
        assert (d / "keep.txt").read_text() == "data"

    def test_directory_with_subdirectory_refused(self, trusted_root):
        d = trusted_root / "nonempty"
        (d / "nested").mkdir(parents=True)
        result = delete_path.invoke({"path": "nonempty"})
        assert "is not empty" in result
        assert (d / "nested").is_dir()

    def test_directory_with_only_ignored_file_refused(self, trusted_root):
        (trusted_root / ".gitignore").write_text("secret.txt\n")
        d = trusted_root / "dirwithignored"
        d.mkdir()
        (d / "secret.txt").write_text("data")
        result = delete_path.invoke({"path": "dirwithignored"})
        assert "is not empty" in result
        assert (d / "secret.txt").exists()


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class TestSecurity:
    """Tests that path escapes are rejected."""

    def test_path_escape_returns_security_error(self, trusted_root):
        result = delete_path.invoke({"path": "../../etc/passwd"})
        assert "Security Error" in result

    def test_absolute_path_outside_returns_security_error(self, trusted_root):
        result = delete_path.invoke({"path": "/etc/passwd"})
        assert "Security Error" in result

    def test_tilde_expansion_rejected(self, trusted_root):
        result = delete_path.invoke({"path": "~"})
        assert "Security Error" in result

    def test_workspace_root_refused(self, trusted_root):
        result = delete_path.invoke({"path": "."})
        assert "Refusing to delete the workspace root" in result
        assert trusted_root.exists()

    @pytest.mark.skipif(
        not hasattr(os, "symlink") or os.name == "nt",
        reason="Symlink support is required for these tests",
    )
    def test_symlink_escape_rejected(self, trusted_root, tmp_path):
        outside_dir = tmp_path.parent / "delete_escape_target"
        outside_dir.mkdir(exist_ok=True)
        link_path = trusted_root / "escape_link"
        os.symlink(outside_dir, link_path)
        result = delete_path.invoke({"path": "escape_link"})
        assert "Security Error" in result
        assert link_path.exists()


# ---------------------------------------------------------------------------
# Missing paths
# ---------------------------------------------------------------------------

class TestMissingPath:
    """Tests for paths that do not exist."""

    def test_nonexistent_path(self, trusted_root):
        result = delete_path.invoke({"path": "no_such.txt"})
        assert result == "Error: Path 'no_such.txt' does not exist."


# ---------------------------------------------------------------------------
# Symlink semantics
# ---------------------------------------------------------------------------

class TestSymlinkSemantics:
    """Tests documenting resolve-then-delete behavior."""

    @pytest.mark.skipif(
        not hasattr(os, "symlink") or os.name == "nt",
        reason="Symlink support is required for these tests",
    )
    def test_symlink_to_in_workspace_target_deletes_target(self, trusted_root):
        target = trusted_root / "target.txt"
        target.write_text("data")
        link = trusted_root / "link.txt"
        os.symlink(target, link)
        result = delete_path.invoke({"path": "link.txt"})
        assert "Successfully deleted file" in result
        # The resolver followed the link, so the target is removed and the
        # symlink itself is left dangling.
        assert not target.exists()
        assert link.is_symlink()
