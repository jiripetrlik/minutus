"""
Unit tests for the write_file @tool function.

Tests cover:
  - New file creation (in root, in subdirectory)
  - Overwriting existing files
  - Automatic parent directory creation
  - Security: path escape rejection

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import write_file

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# New file creation
# ---------------------------------------------------------------------------

class TestNewFile:
    """Tests for creating new files."""

    def test_create_new_file(self, trusted_root):
        result = write_file.invoke({"path": "new.txt", "content": "hello"})
        assert "Successfully wrote 5 characters to new.txt." in result
        assert (trusted_root / "new.txt").read_text() == "hello"

    def test_create_file_in_current_dir(self, trusted_root):
        result = write_file.invoke({"path": "test.txt", "content": "data"})
        assert "Successfully wrote" in result
        assert (trusted_root / "test.txt").read_text() == "data"

    def test_create_file_in_subdirectory(self, trusted_root):
        result = write_file.invoke({"path": "sub/dir/test.txt", "content": "nested"})
        assert "Successfully wrote" in result
        assert (trusted_root / "sub" / "dir" / "test.txt").read_text() == "nested"


# ---------------------------------------------------------------------------
# Overwriting
# ---------------------------------------------------------------------------

class TestOverwrite:
    """Tests for overwriting existing files."""

    def test_overwrite_existing_file(self, trusted_root):
        f = trusted_root / "existing.txt"
        f.write_text("old content")
        result = write_file.invoke({"path": "existing.txt", "content": "new content"})
        assert "Successfully wrote" in result
        assert f.read_text() == "new content"

    def test_overwrite_with_empty_string(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("data here")
        result = write_file.invoke({"path": "file.txt", "content": ""})
        assert "Successfully wrote 0 characters" in result
        assert f.read_text() == ""

    def test_write_unicode_content(self, trusted_root):
        content = "héllo wörld 🎉"
        result = write_file.invoke({"path": "unicode.txt", "content": content})
        assert "Successfully wrote" in result
        assert (trusted_root / "unicode.txt").read_text() == content


# ---------------------------------------------------------------------------
# Parent directory auto-creation
# ---------------------------------------------------------------------------

class TestParentDirCreation:
    """Tests for automatic parent directory creation."""

    def test_auto_create_nested_parent_dirs(self, trusted_root):
        result = write_file.invoke(
            {"path": "a/b/c/d/file.txt", "content": "deep"}
        )
        assert "Successfully wrote" in result
        assert (trusted_root / "a" / "b" / "c" / "d" / "file.txt").read_text() == "deep"

    def test_no_error_when_parent_exists(self, trusted_root):
        (trusted_root / "sub").mkdir()
        result = write_file.invoke({"path": "sub/file.txt", "content": "ok"})
        assert "Successfully wrote" in result
        assert (trusted_root / "sub" / "file.txt").read_text() == "ok"


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class TestSecurity:
    """Tests that path escapes are rejected."""

    def test_path_escape_returns_security_error(self, trusted_root):
        result = write_file.invoke(
            {"path": "../../etc/passwd", "content": "malicious"}
        )
        assert "Security Error" in result

    @pytest.mark.skipif(
        not hasattr(os, "symlink") or os.name == "nt",
        reason="Symlink support is required for these tests",
    )
    def test_symlink_escape_rejected(self, trusted_root, tmp_path):
        outside_dir = tmp_path.parent / "write_escape_target"
        outside_dir.mkdir(exist_ok=True)
        link_path = trusted_root / "escape_link"
        os.symlink(outside_dir, link_path)
        result = write_file.invoke(
            {"path": "escape_link/file.txt", "content": "escaped"}
        )
        assert "Security Error" in result
