"""
Unit tests for gitignore-style ignore-file support (.gitignore and .aiignore).

Tests cover:
  - read_file / edit_file / append_file / write_file refusal on ignored paths
  - write_file refusal for ignored parent directories
  - list_files (recursive and flat) and search_files exclusion
  - negation (`!`), directory-only patterns, and nested ignore files
  - precedence between .gitignore and .aiignore (merged, .aiignore last)
  - default-on behavior and --no-ignore-files opt-out
  - cache invalidation when an ignore file is edited

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import sys
import time
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import (
    IGNORE_FILES,
    WorkspaceIgnore,
    create_append_file_tool,
    create_edit_file_tool,
    create_list_files_tool,
    create_read_file_tool,
    create_search_files_tool,
    create_write_file_tool,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(root, rel_path, content=""):
    f = root / rel_path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    return f


def _listing_paths(output):
    return [
        line
        for line in output.strip().split("\n")
        if line and not line.startswith("... ") and not line.startswith("Directory is empty")
    ]


def _search_result_paths(output):
    paths = []
    for line in output.strip().split("\n"):
        if line.startswith("... ") or line == "No matches found.":
            continue
        parts = line.split(":", 2)
        if len(parts) >= 2:
            paths.append(parts[0])
    return paths


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_ignore_files_order(self):
        assert IGNORE_FILES == (".gitignore", ".aiignore")


# ---------------------------------------------------------------------------
# Matcher unit tests
# ---------------------------------------------------------------------------

class TestWorkspaceIgnore:
    def test_no_ignore_files_means_no_match(self, trusted_root):
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "anything.txt") is None

    def test_disabled_ignores_rules(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        ignore = WorkspaceIgnore(enabled=False)
        assert ignore.check(trusted_root / "secret.txt") is None

    def test_basic_match_returns_source(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        ignore = WorkspaceIgnore(enabled=True)
        source = ignore.check(trusted_root / "secret.txt")
        assert source == trusted_root / ".gitignore"

    def test_negation_reincludes(self, trusted_root):
        _write(trusted_root, ".gitignore", "*.log\n!important.log\n")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "debug.log") is not None
        assert ignore.check(trusted_root / "important.log") is None

    def test_directory_only_pattern(self, trusted_root):
        _write(trusted_root, ".gitignore", "dist/\n")
        _write(trusted_root, "dist/bundle.js")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "dist", is_dir=True) is not None
        assert ignore.check(trusted_root / "dist" / "bundle.js") is not None

    def test_nested_ignore_file_scoped_to_subdir(self, trusted_root):
        _write(trusted_root, "sub/.gitignore", "local.txt\n")
        _write(trusted_root, "sub/local.txt")
        _write(trusted_root, "local.txt")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "sub" / "local.txt") is not None
        assert ignore.check(trusted_root / "local.txt") is None

    def test_deeper_file_overrides_shallower(self, trusted_root):
        _write(trusted_root, ".gitignore", "keep.txt\n")
        _write(trusted_root, "sub/.gitignore", "!keep.txt\n")
        _write(trusted_root, "sub/keep.txt")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "keep.txt") is not None
        assert ignore.check(trusted_root / "sub" / "keep.txt") is None

    def test_aiignore_can_negate_gitignore(self, trusted_root):
        _write(trusted_root, ".gitignore", "*.tmp\n")
        _write(trusted_root, ".aiignore", "!keep.tmp\n")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "keep.tmp") is None
        assert ignore.check(trusted_root / "other.tmp") is not None

    def test_root_itself_never_ignored(self, trusted_root):
        _write(trusted_root, ".gitignore", "*\n")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root, is_dir=True) is None

    def test_path_outside_root_is_not_ignored(self, trusted_root, tmp_path_factory):
        _write(trusted_root, ".gitignore", "*\n")
        ignore = WorkspaceIgnore(enabled=True)
        outside = tmp_path_factory.mktemp("outside") / "x.txt"
        outside.write_text("x")
        assert ignore.check(outside) is None

    def test_empty_ignore_file(self, trusted_root):
        _write(trusted_root, ".gitignore", "")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "x.txt") is None

    def test_malformed_file_does_not_crash(self, trusted_root):
        (trusted_root / ".gitignore").write_bytes(b"\xff\xfe\x00not utf8")
        ignore = WorkspaceIgnore(enabled=True)
        # Read as UTF-8 fails -> treated as absent, no crash.
        assert ignore.check(trusted_root / "x.txt") is None

    def test_crlf_line_endings(self, trusted_root):
        (trusted_root / ".gitignore").write_bytes(b"secret.txt\r\nother.txt\r\n")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "secret.txt") is not None

    def test_cache_invalidation_on_edit(self, trusted_root):
        ignore_file = _write(trusted_root, ".gitignore", "a.txt\n")
        ignore = WorkspaceIgnore(enabled=True)
        assert ignore.check(trusted_root / "b.txt") is None
        # Ensure a distinct mtime even on coarse-grained filesystems.
        time.sleep(0.01)
        ignore_file.write_text("a.txt\nb.txt\n")
        assert ignore.check(trusted_root / "b.txt") is not None


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------

class TestReadFile:
    def test_ignored_file_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt", "top secret\n")
        tool = create_read_file_tool()
        result = tool.invoke({"path": "secret.txt"})
        assert "excluded by ignore rules" in result
        assert ".gitignore" in result
        assert "top secret" not in result

    def test_non_ignored_file_readable(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "public.txt", "hello\n")
        tool = create_read_file_tool()
        result = tool.invoke({"path": "public.txt"})
        assert "1: hello" in result

    def test_disabled_reads_ignored_file(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt", "top secret\n")
        tool = create_read_file_tool(respect_ignore_files=False)
        result = tool.invoke({"path": "secret.txt"})
        assert "1: top secret" in result


# ---------------------------------------------------------------------------
# edit_file
# ---------------------------------------------------------------------------

class TestEditFile:
    def test_ignored_file_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        f = _write(trusted_root, "secret.txt", "old\n")
        tool = create_edit_file_tool()
        result = tool.invoke(
            {"path": "secret.txt", "old_string": "old", "new_string": "new"}
        )
        assert "excluded by ignore rules" in result
        assert f.read_text() == "old\n"

    def test_disabled_edits_ignored_file(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        f = _write(trusted_root, "secret.txt", "old\n")
        tool = create_edit_file_tool(respect_ignore_files=False)
        result = tool.invoke(
            {"path": "secret.txt", "old_string": "old", "new_string": "new"}
        )
        assert "Successfully edited" in result
        assert f.read_text() == "new\n"


# ---------------------------------------------------------------------------
# append_file
# ---------------------------------------------------------------------------

class TestAppendFile:
    def test_ignored_file_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        f = _write(trusted_root, "secret.txt", "old\n")
        tool = create_append_file_tool()
        result = tool.invoke({"path": "secret.txt", "content": "more\n"})
        assert "excluded by ignore rules" in result
        assert f.read_text() == "old\n"

    def test_disabled_appends_ignored_file(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        f = _write(trusted_root, "secret.txt", "old\n")
        tool = create_append_file_tool(respect_ignore_files=False)
        result = tool.invoke({"path": "secret.txt", "content": "more\n"})
        assert "Successfully appended" in result
        assert "more" in f.read_text()


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------

class TestWriteFile:
    def test_ignored_file_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        tool = create_write_file_tool()
        result = tool.invoke({"path": "secret.txt", "content": "x"})
        assert "excluded by ignore rules" in result
        assert not (trusted_root / "secret.txt").exists()

    def test_ignored_parent_dir_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "build/\n")
        tool = create_write_file_tool()
        result = tool.invoke({"path": "build/keep.txt", "content": "x"})
        assert "excluded by ignore rules" in result
        assert not (trusted_root / "build" / "keep.txt").exists()

    def test_write_in_new_allowed_dir_creates_parents(self, trusted_root):
        _write(trusted_root, ".gitignore", "build/\n")
        tool = create_write_file_tool()
        result = tool.invoke({"path": "src/new/deep.txt", "content": "x"})
        assert "Successfully wrote" in result
        assert (trusted_root / "src" / "new" / "deep.txt").read_text() == "x"

    def test_disabled_writes_ignored_file(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        tool = create_write_file_tool(respect_ignore_files=False)
        result = tool.invoke({"path": "secret.txt", "content": "x"})
        assert "Successfully wrote" in result
        assert (trusted_root / "secret.txt").read_text() == "x"


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------

class TestListFiles:
    def test_recursive_hides_ignored(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\ndist/\n")
        _write(trusted_root, "secret.txt")
        _write(trusted_root, "dist/bundle.js")
        _write(trusted_root, "public.txt")
        tool = create_list_files_tool()
        paths = _listing_paths(tool.invoke({"path": ".", "recursive": True}))
        assert "public.txt" in paths
        assert "secret.txt" not in paths
        assert not any(p.startswith("dist") for p in paths)

    def test_flat_hides_ignored(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\ndist/\n")
        _write(trusted_root, "secret.txt")
        _write(trusted_root, "dist/bundle.js")
        _write(trusted_root, "public.txt")
        tool = create_list_files_tool()
        entries = set(tool.invoke({"path": ".", "recursive": False}).split("\n"))
        assert "public.txt" in entries
        assert "secret.txt" not in entries
        assert "dist" not in entries

    def test_explicit_ignored_dir_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "dist/\n")
        _write(trusted_root, "dist/bundle.js")
        tool = create_list_files_tool()
        result = tool.invoke({"path": "dist", "recursive": True})
        assert "excluded by ignore rules" in result

    def test_explicit_ignored_file_path_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt")
        tool = create_list_files_tool()
        # A file path is not a directory, so it errors on that first.
        result = tool.invoke({"path": "secret.txt"})
        assert "is not a directory" in result

    def test_disabled_lists_ignored(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt")
        tool = create_list_files_tool(respect_ignore_files=False)
        paths = _listing_paths(tool.invoke({"path": ".", "recursive": True}))
        assert "secret.txt" in paths


# ---------------------------------------------------------------------------
# search_files
# ---------------------------------------------------------------------------

class TestSearchFiles:
    def test_search_skips_ignored_files(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt", "findme\n")
        _write(trusted_root, "public.txt", "findme\n")
        tool = create_search_files_tool()
        paths = _search_result_paths(tool.invoke({"query": "findme", "path": "."}))
        assert "public.txt" in paths
        assert "secret.txt" not in paths

    def test_search_does_not_traverse_ignored_dirs(self, trusted_root):
        _write(trusted_root, ".gitignore", "dist/\n")
        _write(trusted_root, "dist/bundle.js", "only_in_dist\n")
        tool = create_search_files_tool()
        result = tool.invoke({"query": "only_in_dist", "path": "."})
        assert result == "No matches found."

    def test_search_explicit_ignored_file_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt", "findme\n")
        tool = create_search_files_tool()
        result = tool.invoke({"query": "findme", "path": "secret.txt"})
        assert "excluded by ignore rules" in result

    def test_search_explicit_ignored_dir_refused(self, trusted_root):
        _write(trusted_root, ".gitignore", "dist/\n")
        _write(trusted_root, "dist/bundle.js", "findme\n")
        tool = create_search_files_tool()
        result = tool.invoke({"query": "findme", "path": "dist"})
        assert "excluded by ignore rules" in result

    def test_disabled_searches_ignored(self, trusted_root):
        _write(trusted_root, ".gitignore", "secret.txt\n")
        _write(trusted_root, "secret.txt", "findme\n")
        tool = create_search_files_tool(respect_ignore_files=False)
        paths = _search_result_paths(tool.invoke({"query": "findme", "path": "."}))
        assert "secret.txt" in paths


# ---------------------------------------------------------------------------
# Negation, precedence, and nested files end-to-end
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_negation_reincludes_file(self, trusted_root):
        _write(trusted_root, ".gitignore", "*.log\n!keep.log\n")
        _write(trusted_root, "keep.log", "hello\n")
        _write(trusted_root, "drop.log", "hello\n")
        tool = create_read_file_tool()
        assert "1: hello" in tool.invoke({"path": "keep.log"})
        assert "excluded" in tool.invoke({"path": "drop.log"})

    def test_aiignore_last_wins(self, trusted_root):
        _write(trusted_root, ".gitignore", "*.tmp\n")
        _write(trusted_root, ".aiignore", "!keep.tmp\n")
        _write(trusted_root, "keep.tmp", "hi\n")
        tool = create_read_file_tool()
        assert "1: hi" in tool.invoke({"path": "keep.tmp"})

    def test_nested_ignore_scoped(self, trusted_root):
        _write(trusted_root, "sub/.gitignore", "local.txt\n")
        _write(trusted_root, "sub/local.txt", "hi\n")
        tool = create_read_file_tool()
        assert "excluded" in tool.invoke({"path": "sub/local.txt"})
