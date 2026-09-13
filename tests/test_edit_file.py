"""
Unit tests for the edit_file @tool function.

Tests cover:
  - Successful edits (unique string replacement, multiline, empty replacement,
    longer replacement)
  - String not found error (and file unchanged)
  - Non-unique string error (and file unchanged)
  - Errors: non-existent file, path escape, directory not file

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import edit_file

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Successful edits
# ---------------------------------------------------------------------------

class TestSuccessfulEdit:
    """Tests for successful edit operations."""

    def test_edit_replaces_unique_string(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("hello world")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "hello",
            "new_string": "goodbye",
        })
        assert result == "Successfully edited file.txt."
        assert f.read_text() == "goodbye world"

    def test_edit_multiline_old_string(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("line1\nline2\nline3\n")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "line1\nline2",
            "new_string": "replaced1\nreplaced2",
        })
        assert result == "Successfully edited file.txt."
        assert f.read_text() == "replaced1\nreplaced2\nline3\n"

    def test_edit_replaces_with_empty_string(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("afoob")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "foo",
            "new_string": "",
        })
        assert result == "Successfully edited file.txt."
        assert f.read_text() == "ab"

    def test_edit_replaces_with_longer_string(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("a b c")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "b",
            "new_string": "bbb",
        })
        assert result == "Successfully edited file.txt."
        assert f.read_text() == "a bbb c"


# ---------------------------------------------------------------------------
# String not found
# ---------------------------------------------------------------------------

class TestStringNotFound:
    """Tests for the case where old_string is not in the file."""

    def test_old_string_not_found(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("hello world")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "xyz",
            "new_string": "abc",
        })
        assert "Error: old_string not found in file.txt." in result

    def test_file_unchanged_on_not_found(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("hello world")
        edit_file.invoke({
            "path": "file.txt",
            "old_string": "xyz",
            "new_string": "abc",
        })
        assert f.read_text() == "hello world"


# ---------------------------------------------------------------------------
# Non-unique string
# ---------------------------------------------------------------------------

class TestNonUniqueString:
    """Tests for the case where old_string appears multiple times."""

    def test_old_string_not_unique(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("foo\nfoo\n")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "foo",
            "new_string": "bar",
        })
        assert "Error: old_string is not unique (found 2 times)" in result

    def test_file_unchanged_on_non_unique(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("foo\nfoo\n")
        edit_file.invoke({
            "path": "file.txt",
            "old_string": "foo",
            "new_string": "bar",
        })
        assert f.read_text() == "foo\nfoo\n"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class TestErrors:
    """Tests for error conditions."""

    def test_nonexistent_file(self, trusted_root):
        result = edit_file.invoke({
            "path": "no_such.txt",
            "old_string": "a",
            "new_string": "b",
        })
        assert "Error: File 'no_such.txt' does not exist." in result

    def test_path_escape_returns_security_error(self, trusted_root):
        result = edit_file.invoke({
            "path": "../../etc/passwd",
            "old_string": "a",
            "new_string": "b",
        })
        assert "Security Error" in result

    def test_edit_directory_not_file(self, trusted_root):
        (trusted_root / "mydir").mkdir()
        result = edit_file.invoke({
            "path": "mydir",
            "old_string": "a",
            "new_string": "b",
        })
        assert "Error: File 'mydir' does not exist." in result
# ---------------------------------------------------------------------------
# Literal replacement semantics (no escape processing)
# ---------------------------------------------------------------------------

class TestLiteralReplacement:
    """edit_file performs a literal replace and never translates escapes."""

    def test_edit_does_not_translate_backslash_n(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text('"lineA\\n",\n"lineB\\n",\n')
        # new_string contains the two characters backslash + n.
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": '"lineB\\n",',
            "new_string": '"lineB2\\n",',
        })
        assert result == "Successfully edited file.txt."
        # The literal backslash+n must survive unchanged (2 chars, not a newline).
        assert f.read_text() == '"lineA\\n",\n"lineB2\\n",\n'
        # Explicitly: the file must still contain the backslash-n sequence.
        assert "\\n" in f.read_text()

    def test_edit_real_newline_in_new_string_is_written(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("aXb\n")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "X",
            "new_string": "\n",
        })
        assert result == "Successfully edited file.txt."
        # A real newline argument becomes a real newline byte.
        assert f.read_text() == "a\nb\n"

    def test_edit_matches_exact_trailing_whitespace(self, trusted_root):
        f = trusted_root / "file.txt"
        f.write_text("keep   \ndrop\n")
        result = edit_file.invoke({
            "path": "file.txt",
            "old_string": "keep   \n",
            "new_string": "keep\n",
        })
        assert result == "Successfully edited file.txt."
        assert f.read_text() == "keep\ndrop\n"