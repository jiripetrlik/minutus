"""
Unit tests for the list_files @tool function.

Tests cover:
  - Flat listing (files + dirs, empty directory, non-existent path, file not dir)
  - Recursive listing (nested files, empty directory, relative paths)
  - Truncation at 500 items
  - Security: path escape rejection

Note: IGNORE_DIRS filtering tests are already covered in test_ignore_dirs.py
and are NOT duplicated here.

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import list_files

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Flat listing
# ---------------------------------------------------------------------------

class TestFlatListing:
    """Tests for non-recursive list_files."""

    def test_flat_listing_shows_files_and_dirs(self, trusted_root):
        (trusted_root / "file1.txt").write_text("a")
        (trusted_root / "file2.txt").write_text("b")
        (trusted_root / "subdir").mkdir()
        result = list_files.invoke({"path": ".", "recursive": False})
        entries = set(result.strip().split("\n"))
        assert "file1.txt" in entries
        assert "file2.txt" in entries
        assert "subdir" in entries

    def test_flat_listing_empty_directory(self, trusted_root):
        result = list_files.invoke({"path": ".", "recursive": False})
        assert result == "Directory is empty."

    def test_flat_listing_nonexistent_path(self, trusted_root):
        result = list_files.invoke({"path": "no_such_dir", "recursive": False})
        assert "Error: 'no_such_dir' is not a directory." in result

    def test_flat_listing_file_not_directory(self, trusted_root):
        (trusted_root / "file.txt").write_text("hello")
        result = list_files.invoke({"path": "file.txt", "recursive": False})
        assert "Error: 'file.txt' is not a directory." in result


# ---------------------------------------------------------------------------
# Recursive listing
# ---------------------------------------------------------------------------

class TestRecursiveListing:
    """Tests for recursive list_files."""

    def test_recursive_listing_shows_nested_files(self, trusted_root):
        (trusted_root / "a" / "b").mkdir(parents=True)
        (trusted_root / "a" / "b" / "c.txt").write_text("deep")
        (trusted_root / "top.txt").write_text("top")
        result = list_files.invoke({"path": ".", "recursive": True})
        entries = set(result.strip().split("\n"))
        assert "top.txt" in entries
        assert any("c.txt" in e for e in entries)
        # The path should be relative to the requested path (".")
        assert os.path.join("a", "b", "c.txt") in entries

    def test_recursive_listing_empty_directory(self, trusted_root):
        result = list_files.invoke({"path": ".", "recursive": True})
        assert result == "Directory is empty."

    def test_recursive_paths_are_relative_to_requested_path(self, trusted_root):
        (trusted_root / "sub").mkdir()
        (trusted_root / "sub" / "file1.txt").write_text("1")
        (trusted_root / "sub" / "nested").mkdir()
        (trusted_root / "sub" / "nested" / "file2.txt").write_text("2")
        result = list_files.invoke({"path": "sub", "recursive": True})
        entries = set(result.strip().split("\n"))
        # Paths should be relative to "sub", not to root
        assert "file1.txt" in entries
        assert os.path.join("nested", "file2.txt") in entries
        # Should NOT contain "sub/" prefix
        assert not any(e.startswith("sub" + os.sep) for e in entries)


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

class TestTruncation:
    """Tests for the 500-item truncation logic."""

    def test_truncation_at_configured_limit(self, trusted_root):
        from minutus.minutus import create_list_files_tool
        for i in range(4):
            (trusted_root / f"file_{i}.txt").write_text("x")
        result = create_list_files_tool(3).invoke({"path": ".", "recursive": False})
        lines = result.strip().split("\n")
        assert len([line for line in lines if not line.startswith("... ")]) == 3
        assert "showing first 3 of 4 lines" in lines[-1]

    def test_truncation_exactly_500_not_truncated(self, trusted_root):
        for i in range(500):
            (trusted_root / f"file_{i}.txt").write_text("x")
        result = list_files.invoke({"path": ".", "recursive": False})
        assert "Directory contains" not in result
        assert "... Use search_files" not in result
        lines = result.strip().split("\n")
        assert len(lines) == 500


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

class TestSecurity:
    """Tests that path escapes are rejected."""

    def test_path_escape_returns_security_error(self, trusted_root):
        result = list_files.invoke({"path": "../../etc/passwd"})
        assert "Security Error" in result
