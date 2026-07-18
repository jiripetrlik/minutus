"""
Unit tests for the append_file @tool function.

Tests cover:
  - Append to file with trailing newline (no extra newline inserted)
  - Append to file without trailing newline (auto-insert newline)
  - Append to empty file
  - Multiple appends
  - Unicode content
  - Errors: non-existent file, path escape, directory not file

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import append_file

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Append to file with trailing newline
# ---------------------------------------------------------------------------

class TestAppendWithTrailingNewline:
    """Tests for appending to a file that ends with a newline."""

    def test_append_to_file_with_newline(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("line1\n")
        result = append_file.invoke({"path": "file.txt", "content": "line2"})
        assert "Successfully appended 5 characters to file.txt." in result
        assert f.read_text() == "line1\nline2"

    def test_return_message(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("data\n")
        result = append_file.invoke({"path": "file.txt", "content": "12345"})
        assert result == "Successfully appended 5 characters to file.txt."


# ---------------------------------------------------------------------------
# Append to file without trailing newline
# ---------------------------------------------------------------------------

class TestAppendWithoutTrailingNewline:
    """Tests for appending to a file that does not end with a newline."""

    def test_append_to_file_without_newline(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("line1")
        result = append_file.invoke({"path": "file.txt", "content": "line2"})
        assert "Successfully appended" in result
        assert f.read_text() == "line1\nline2"

    def test_auto_newline_inserted(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("abc")
        append_file.invoke({"path": "file.txt", "content": "def"})
        content = f.read_text()
        assert "\n" in content
        assert content == "abc\ndef"


# ---------------------------------------------------------------------------
# Append to empty file
# ---------------------------------------------------------------------------

class TestAppendToEmptyFile:
    """Tests for appending to a file that is empty (0 bytes)."""

    def test_append_to_empty_file(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("")
        result = append_file.invoke({"path": "file.txt", "content": "hello"})
        assert "Successfully appended 5 characters" in result
        assert f.read_text() == "hello"


# ---------------------------------------------------------------------------
# Multiple appends
# ---------------------------------------------------------------------------

class TestAppendMultipleTimes:
    """Tests for appending multiple times to the same file."""

    def test_append_then_append_again(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("start")
        append_file.invoke({"path": "file.txt", "content": "a"})
        append_file.invoke({"path": "file.txt", "content": "b"})
        assert f.read_text() == "start\na\nb"


# ---------------------------------------------------------------------------
# Unicode content
# ---------------------------------------------------------------------------

class TestAppendUnicode:
    """Tests for appending unicode content."""

    def test_append_unicode_content(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("line1\n")
        content = "héllo"
        result = append_file.invoke({"path": "file.txt", "content": content})
        assert "Successfully appended 5 characters" in result
        assert f.read_text() == "line1\nhéllo"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestErrors:
    """Tests for error conditions."""

    def test_nonexistent_file(self, trusted_root):
        result = append_file.invoke({"path": "no_such.txt", "content": "data"})
        assert "Error: File 'no_such.txt' does not exist." in result
        assert "Use write_file to create it first." in result

    def test_path_escape_returns_security_error(self, trusted_root):
        result = append_file.invoke(
            {"path": "../../etc/passwd", "content": "data"}
        )
        assert "Security Error" in result

    def test_append_to_directory(self, trusted_root):
        (trusted_root / "mydir").mkdir()
        result = append_file.invoke({"path": "mydir", "content": "data"})
        assert "Error: File 'mydir' does not exist." in result
