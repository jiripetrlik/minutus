"""
Unit tests for the search_files @tool function.

Tests cover:
  - Regex matching (case-sensitive, case-insensitive)
  - Literal fallback on invalid regex
  - Result truncation at 30 matches
  - No matches
  - Error handling (UnicodeDecodeError, PermissionError, non-existent path,
    path escape)
  - Path format (relative paths, result line format)

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

from minutus.minutus import search_files
import minutus.minutus as minutus_mod

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Regex matching
# ---------------------------------------------------------------------------

class TestRegexMatching:
    """Tests for regex-based search."""

    def test_regex_match(self, trusted_root):
        (trusted_root / "file.txt").write_text("hello world\n")
        result = search_files.invoke({"query": "hel.*world", "path": "."})
        assert "hello world" in result
        assert "file.txt" in result

    def test_regex_case_sensitive(self, trusted_root):
        (trusted_root / "file.txt").write_text("Hello world\n")
        result = search_files.invoke({"query": "hello", "path": "."})
        assert result == "No matches found."

    def test_regex_case_insensitive(self, trusted_root):
        (trusted_root / "file.txt").write_text("Hello world\n")
        result = search_files.invoke({"query": "(?i)hello", "path": "."})
        assert "Hello world" in result


# ---------------------------------------------------------------------------
# Literal fallback
# ---------------------------------------------------------------------------

class TestLiteralFallback:
    """Tests that invalid regex falls back to literal substring matching."""

    def test_invalid_regex_falls_back_to_literal(self, trusted_root):
        (trusted_root / "file.txt").write_text("this has [invalid in it\n")
        result = search_files.invoke({"query": "[invalid", "path": "."})
        # "[invalid" is not valid regex, so it falls back to literal match
        assert "this has [invalid in it" in result

    def test_literal_match_special_chars(self, trusted_root):
        # "*" is not a valid regex by itself, so it falls back to literal
        (trusted_root / "file.txt").write_text("a*b\n")
        result = search_files.invoke({"query": "*", "path": "."})
        assert "a*b" in result


# ---------------------------------------------------------------------------
# Result truncation
# ---------------------------------------------------------------------------

class TestResultTruncation:
    """Tests for the 30-match truncation."""

    def test_truncation_at_configured_limit(self, trusted_root):
        from minutus.minutus import create_search_files_tool
        for i in range(4):
            (trusted_root / f"file_{i}.txt").write_text("uniquetrunc\n")
        result = create_search_files_tool(3).invoke(
            {"query": "uniquetrunc", "path": "."}
        )
        lines = result.strip().split("\n")
        result_lines = [line for line in lines if not line.startswith("... ")]
        assert len(result_lines) == 3
        assert "showing first 3 matching lines" in lines[-1]

    def test_exactly_29_not_truncated(self, trusted_root):
        # The code truncates at len(results) >= 30, so 29 matches must NOT
        # trigger truncation.
        for i in range(29):
            (trusted_root / f"file_{i}.txt").write_text("exactly29\n")
        result = search_files.invoke({"query": "exactly29", "path": "."})
        assert "Results truncated" not in result
        lines = result.strip().split("\n")
        assert len(lines) == 29


# ---------------------------------------------------------------------------
# No matches
# ---------------------------------------------------------------------------

class TestNoMatches:
    """Tests for the no-matches case."""

    def test_no_matches_returns_message(self, trusted_root):
        (trusted_root / "file.txt").write_text("hello world\n")
        result = search_files.invoke({"query": "nomatch", "path": "."})
        assert result == "No matches found."


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Tests for error and edge-case handling during search."""

    def test_unicode_decode_error_skipped(self, trusted_root):
        # Valid UTF-8 file with the search term
        (trusted_root / "good.txt").write_text("findme here\n")
        # Binary file that also contains the search term but can't be decoded
        (trusted_root / "bad.bin").write_bytes(b"\xff\xfe findme \x00")
        result = search_files.invoke({"query": "findme", "path": "."})
        assert "good.txt" in result
        assert "bad.bin" not in result

    def test_nonexistent_path(self, trusted_root):
        result = search_files.invoke({"query": "test", "path": "no_such_dir"})
        assert "Error: Path 'no_such_dir' does not exist." in result

    def test_path_escape_returns_security_error(self, trusted_root):
        result = search_files.invoke({"query": "test", "path": "../../etc"})
        assert "Security Error" in result

    @pytest.mark.skipif(
        os.name == "nt",
        reason="Permission handling differs on Windows",
    )
    def test_permission_error_skipped(self, trusted_root):
        # Skip when running as root — root can read anything
        if os.geteuid() == 0:
            pytest.skip("Cannot test permission errors as root")
        (trusted_root / "readable.txt").write_text("findme ok\n")
        (trusted_root / "noperm.txt").write_text("findme hidden\n")
        os.chmod(trusted_root / "noperm.txt", 0o000)
        try:
            result = search_files.invoke({"query": "findme", "path": "."})
            assert "readable.txt" in result
            assert "noperm.txt" not in result
        finally:
            # Restore permissions so cleanup works
            os.chmod(trusted_root / "noperm.txt", 0o644)


# ---------------------------------------------------------------------------
# Path format
# ---------------------------------------------------------------------------

class TestPathFormat:
    """Tests for the format of search result paths and lines."""

    def test_results_use_relative_paths_from_trusted_root(self, trusted_root):
        (trusted_root / "sub").mkdir()
        (trusted_root / "sub" / "file.txt").write_text("findme\n")
        result = search_files.invoke({"query": "findme", "path": "."})
        # Path should be relative to TRUSTED_ROOT, not absolute
        assert "sub/file.txt" in result or os.path.join("sub", "file.txt") in result
        assert "/tmp/" not in result or str(trusted_root) not in result

    def test_result_line_format(self, trusted_root):
        (trusted_root / "file.txt").write_text("findme\n")
        result = search_files.invoke({"query": "findme", "path": "."})
        # Format: filepath:line_number: content
        line = result.strip().split("\n")[0]
        parts = line.split(":", 2)
        assert len(parts) == 3
        assert parts[0] == "file.txt"
        assert parts[1] == "1"
        assert parts[2] == " findme"
