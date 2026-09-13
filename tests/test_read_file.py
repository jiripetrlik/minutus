"""
Unit tests for the read_file @tool function.

Tests cover:
  - Basic reading with line numbers
  - Empty and single-line files
  - Line ranges (start_line, end_line, clamping, out-of-range)
  - Truncation at 2000 lines
  - Errors: non-existent file, path escape, directory, non-UTF-8

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import read_file

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Basic read
# ---------------------------------------------------------------------------

class TestBasicRead:
    """Tests for basic file reading with line numbers."""

    def test_read_file_with_line_numbers(self, trusted_root):
        (trusted_root / "file.txt").write_text("line1\nline2\nline3\n")
        result = read_file.invoke({"path": "file.txt"})
        lines = result.split("\n")
        assert lines[0] == "1: line1"
        assert lines[1] == "2: line2"
        assert lines[2] == "3: line3"

    def test_read_empty_file(self, trusted_root):
        (trusted_root / "empty.txt").write_text("")
        result = read_file.invoke({"path": "empty.txt"})
        assert result == ""

    def test_read_single_line_file(self, trusted_root):
        (trusted_root / "single.txt").write_text("hello\n")
        result = read_file.invoke({"path": "single.txt"})
        assert result == "1: hello"

    def test_read_file_no_trailing_newline(self, trusted_root):
        (trusted_root / "file.txt").write_text("line1\nline2")
        result = read_file.invoke({"path": "file.txt"})
        lines = result.split("\n")
        assert lines[0] == "1: line1"
        assert lines[1] == "2: line2"


# ---------------------------------------------------------------------------
# Line ranges
# ---------------------------------------------------------------------------

class TestLineRanges:
    """Tests for start_line / end_line parameters."""

    def test_start_line_only(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt", "start_line": 3})
        lines = result.split("\n")
        assert len(lines) == 8
        assert lines[0] == "3: line3"
        assert lines[-1] == "10: line10"

    def test_end_line_only(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt", "end_line": 5})
        lines = result.split("\n")
        assert len(lines) == 5
        assert lines[0] == "1: line1"
        assert lines[-1] == "5: line5"

    def test_start_and_end_line(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 11)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({
            "path": "file.txt",
            "start_line": 3,
            "end_line": 7,
        })
        lines = result.split("\n")
        assert len(lines) == 5
        assert lines[0] == "3: line3"
        assert lines[-1] == "7: line7"

    def test_start_line_below_1_clamped(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 6)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt", "start_line": -5})
        lines = result.split("\n")
        assert lines[0] == "1: line1"
        assert len(lines) == 5

    def test_start_line_zero_clamped(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 6)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt", "start_line": 0})
        lines = result.split("\n")
        assert lines[0] == "1: line1"

    def test_end_line_exceeds_total_clamped(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 6)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt", "end_line": 100})
        lines = result.split("\n")
        assert len(lines) == 5
        assert lines[-1] == "5: line5"

    def test_start_line_beyond_total(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 6)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt", "start_line": 10})
        # No lines in range — output is empty string (no truncated_msg)
        assert result == ""


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------

class TestTruncation:
    """Tests for the 2000-line truncation logic."""

    def test_truncation_at_configured_limit(self, trusted_root):
        from minutus.minutus import create_read_file_tool
        content = "\n".join(f"line{i}" for i in range(1, 12)) + "\n"
        (trusted_root / "big.txt").write_text(content)
        result = create_read_file_tool(10).invoke({"path": "big.txt"})
        lines = result.split("\n")
        assert len(lines) == 11
        assert lines[0] == "1: line1"
        assert lines[9] == "10: line10"
        assert "Truncated" in lines[10]

    def test_no_truncation_when_range_under_2000(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 2502)) + "\n"
        (trusted_root / "big.txt").write_text(content)
        result = read_file.invoke({
            "path": "big.txt",
            "start_line": 1,
            "end_line": 100,
        })
        lines = result.split("\n")
        assert len(lines) == 100
        assert "File truncated" not in result

    def test_no_truncation_when_total_under_2000(self, trusted_root):
        content = "\n".join(f"line{i}" for i in range(1, 101)) + "\n"
        (trusted_root / "file.txt").write_text(content)
        result = read_file.invoke({"path": "file.txt"})
        assert "File truncated" not in result
        assert len(result.split("\n")) == 100


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestErrors:
    """Tests for error conditions."""

    def test_nonexistent_file(self, trusted_root):
        result = read_file.invoke({"path": "no_such.txt"})
        assert "Error: File 'no_such.txt' does not exist." in result

    def test_path_escape_returns_security_error(self, trusted_root):
        result = read_file.invoke({"path": "../../etc/passwd"})
        assert "Security Error" in result

    def test_directory_not_file(self, trusted_root):
        (trusted_root / "mydir").mkdir()
        result = read_file.invoke({"path": "mydir"})
        assert "Error: File 'mydir' does not exist." in result

    def test_non_utf8_file_raises_unicode_decode_error(self, trusted_root):
        f = trusted_root / "binary.txt"
        f.write_bytes(b"\xff\xfe\x00binary garbage")
        # The function opens with encoding="utf-8" and has no try/except
        # for UnicodeDecodeError, so it should propagate.
        with pytest.raises(UnicodeDecodeError):
            read_file.invoke({"path": "binary.txt"})
# ---------------------------------------------------------------------------
# Line fidelity (trailing whitespace and line endings)
# ---------------------------------------------------------------------------

class TestLineFidelity:
    """read_file preserves real bytes while hiding only line terminators."""

    def test_read_preserves_trailing_whitespace(self, trusted_root):
        (trusted_root / "file.txt").write_text("a   \nb\n")
        result = read_file.invoke({"path": "file.txt"})
        lines = result.split("\n")
        assert lines[0] == "1: a   "
        assert lines[1] == "2: b"

    def test_read_preserves_trailing_tab(self, trusted_root):
        (trusted_root / "file.txt").write_text("a\t\n")
        result = read_file.invoke({"path": "file.txt"})
        assert result == "1: a\t"

    def test_read_hides_crlf_terminator(self, trusted_root):
        (trusted_root / "file.txt").write_bytes(b"line1\r\nline2\r\n")
        result = read_file.invoke({"path": "file.txt"})
        assert "\r" not in result
        lines = result.split("\n")
        assert lines[0] == "1: line1"
        assert lines[1] == "2: line2"

    def test_show_whitespace_marks_trailing_spaces(self, trusted_root):
        (trusted_root / "file.txt").write_text("a   \nb\n")
        result = read_file.invoke({"path": "file.txt", "show_whitespace": True})
        lines = result.split("\n")
        # Three trailing spaces rendered as three visible markers.
        assert lines[0] == "1: a\u2420\u2420\u2420"
        assert lines[1] == "2: b"

    def test_show_whitespace_marks_trailing_tab(self, trusted_root):
        (trusted_root / "file.txt").write_text("a\t\n")
        result = read_file.invoke({"path": "file.txt", "show_whitespace": True})
        assert result == "1: a\u2409"

    def test_show_whitespace_no_trailing_whitespace_unchanged(self, trusted_root):
        (trusted_root / "file.txt").write_text("a b\n")
        result = read_file.invoke({"path": "file.txt", "show_whitespace": True})
        assert result == "1: a b"