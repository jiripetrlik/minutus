"""
Unit tests for load_files_context() — reads multiple files and wraps each
file's content in a markdown code block.

Tests cover:
  - Single file (basic formatting, multi-line content)
  - Multiple files (ordering, joining)
  - Empty file
  - Output format verification
  - Errors: non-existent file, directory path, mixed valid + invalid

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest
import typer

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import load_files_context

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Single file
# ---------------------------------------------------------------------------

class TestSingleFile:
    """Tests for loading context from a single file."""

    def test_single_file_basic(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello")
        result = load_files_context([str(f)])
        assert "```" in result
        assert "hello" in result
        assert str(f) in result

    def test_single_file_exact_format(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("hello")
        result = load_files_context([str(f)])
        assert result == f"```{f}\nhello\n```"

    def test_single_file_multiline_content(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("line1\nline2\nline3")
        result = load_files_context([str(f)])
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result
        assert result == f"```{f}\nline1\nline2\nline3\n```"

    def test_single_file_empty_content(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = load_files_context([str(f)])
        assert result == f"```{f}\n\n```"


# ---------------------------------------------------------------------------
# Multiple files
# ---------------------------------------------------------------------------

class TestMultipleFiles:
    """Tests for loading context from multiple files."""

    def test_two_files_both_present(self, tmp_path):
        f1 = tmp_path / "file1.txt"
        f2 = tmp_path / "file2.txt"
        f1.write_text("content1")
        f2.write_text("content2")
        result = load_files_context([str(f1), str(f2)])
        assert "content1" in result
        assert "content2" in result
        assert str(f1) in result
        assert str(f2) in result

    def test_two_files_exact_format(self, tmp_path):
        f1 = tmp_path / "file1.txt"
        f2 = tmp_path / "file2.txt"
        f1.write_text("content1")
        f2.write_text("content2")
        result = load_files_context([str(f1), str(f2)])
        expected = f"```{f1}\ncontent1\n```\n```{f2}\ncontent2\n```"
        assert result == expected

    def test_three_files_in_order(self, tmp_path):
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.txt"
            f.write_text(f"data{i}")
            files.append(str(f))
        result = load_files_context(files)
        # Verify order: file0 block comes before file1 block, etc.
        pos0 = result.index("data0")
        pos1 = result.index("data1")
        pos2 = result.index("data2")
        assert pos0 < pos1 < pos2

    def test_multiple_files_joined_with_single_newline(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("aaa")
        f2.write_text("bbb")
        result = load_files_context([str(f1), str(f2)])
        # Blocks are joined with a single "\n"
        assert result == f"```{f1}\naaa\n```\n```{f2}\nbbb\n```"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

class TestOutputFormatting:
    """Tests for the exact output format of code blocks."""

    def test_code_block_starts_with_backticks_and_path(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = load_files_context([str(f)])
        assert result.startswith(f"```{f}")

    def test_code_block_ends_with_closing_backticks(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        result = load_files_context([str(f)])
        assert result.endswith("```")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestErrors:
    """Tests for error conditions."""

    def test_nonexistent_file_raises_typer_exit(self, tmp_path):
        with pytest.raises(typer.Exit) as exc_info:
            load_files_context([str(tmp_path / "no_such.txt")])
        assert exc_info.value.exit_code != 0

    def test_one_valid_one_nonexistent_raises(self, tmp_path):
        good = tmp_path / "good.txt"
        good.write_text("ok")
        bad = str(tmp_path / "bad.txt")
        with pytest.raises(typer.Exit) as exc_info:
            load_files_context([str(good), bad])
        assert exc_info.value.exit_code != 0

    def test_directory_raises_typer_exit(self, tmp_path):
        # Passing a directory path should cause a read error
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        with pytest.raises(typer.Exit) as exc_info:
            load_files_context([str(subdir)])
        assert exc_info.value.exit_code != 0
