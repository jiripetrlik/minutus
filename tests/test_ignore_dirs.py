"""
Unit tests for IGNORE_DIRS filtering — verifying that ignored directories
(.git, node_modules, __pycache__, venv, .venv, dist, build) are correctly
excluded from recursive operations in list_files and search_files.

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import IGNORE_DIRS, list_files, search_files
import minutus.minutus as minutus_mod

# Register the 'unit' marker so pytest doesn't warn about unknown markers.
# This allows running only unit tests (no API key needed) via:
#   pytest -m unit
pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_tree(trusted_root):
    """
    Build a deterministic directory tree inside trusted_root that includes
    both ignored and non-ignored directories at multiple nesting levels.

    Ignored directories (top-level):
      .git/config              — "git_config_content"
      node_modules/package.js  — "require('foo') findme"
      __pycache__/module.pyc   — "binary_junk findme"
      venv/bin/activate        — "activation_script findme"
      .venv/lib/placeholder.txt — "venv_lib findme"
      dist/bundle.js           — "compiled_output findme"
      build/output.o           — "object_file findme"

    Nested ignored directory (inside a non-ignored dir):
      regular_dir/nested_ignore/.git/config — "nested_git_secret findme"

    Non-ignored directories with searchable content:
      normal_file.txt          — "findme hello world"
      regular_dir/file_a.txt   — "hello world"
      sub/file_b.txt            — "hello world findme"
      sub/deeper/file_c.txt     — "deep findme content"

    Edge-case directories (should NOT be filtered):
      NODE_MODULES/upper.txt   — "uppercase_dir findme"  (case differs)
      .gitter/sub_file.txt     — "partial_name findme"   (substring differs)
    """
    def _create_file(rel_path, content):
        f = trusted_root / rel_path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

    # --- Non-ignored directories with searchable content ---
    _create_file("normal_file.txt", "findme hello world")
    _create_file("regular_dir/file_a.txt", "hello world")
    _create_file("sub/file_b.txt", "hello world findme")
    _create_file("sub/deeper/file_c.txt", "deep findme content")

    # --- Ignored directories (top-level), each with searchable content ---
    _create_file(".git/config", "git_config_content")
    _create_file("node_modules/package.js", "require('foo') findme")
    _create_file("__pycache__/module.pyc", "binary_junk findme")
    _create_file("venv/bin/activate", "activation_script findme")
    _create_file(".venv/lib/placeholder.txt", "venv_lib findme")
    _create_file("dist/bundle.js", "compiled_output findme")
    _create_file("build/output.o", "object_file findme")

    # --- Nested ignored directory inside a non-ignored directory ---
    _create_file("regular_dir/nested_ignore/.git/config", "nested_git_secret findme")

    # --- Edge-case directories (should NOT be filtered) ---
    _create_file("NODE_MODULES/upper.txt", "uppercase_dir findme")
    _create_file(".gitter/sub_file.txt", "partial_name findme")

    return trusted_root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contains_ignored_dir(path_str):
    """Check if a path contains any IGNORE_DIRS component."""
    parts = Path(path_str).parts
    return any(part in IGNORE_DIRS for part in parts)


def _listing_paths(output):
    """Parse list_files output and return list of file-path entries.

    Skips non-path lines like 'Directory is empty.', the truncation header,
    and the '...' continuation marker.
    """
    paths = []
    for line in output.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("... "):
            continue
        if line.startswith("Directory is empty"):
            continue
        if line.startswith("Directory contains"):
            continue
        paths.append(line)
    return paths


def _search_result_paths(output):
    """Parse search_files output and return list of file paths from result lines.

    Each result line has the format:  filepath:line_number: content
    Skips the 'No matches found.' line and the '...' truncation marker.
    """
    paths = []
    for line in output.strip().split("\n"):
        if line.startswith("... "):
            continue
        if line == "No matches found.":
            continue
        # Format: filepath:line_number: content
        parts = line.split(":", 2)
        if len(parts) >= 2:
            paths.append(parts[0])
    return paths


# ---------------------------------------------------------------------------
# 1. IGNORE_DIRS data structure
# ---------------------------------------------------------------------------

class TestIgnoreDirsSet:
    """Tests for the IGNORE_DIRS data structure itself."""

    def test_ignore_dirs_contents(self):
        """The set must contain exactly the 7 expected directory names."""
        expected = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}
        assert IGNORE_DIRS == expected

    def test_ignore_dirs_is_set(self):
        """IGNORE_DIRS must be a set for O(1) case-sensitive membership lookup."""
        assert isinstance(IGNORE_DIRS, set)


# ---------------------------------------------------------------------------
# 2. list_files recursive mode
# ---------------------------------------------------------------------------

class TestListFilesRecursive:
    """Tests that IGNORE_DIRS filtering works in list_files recursive mode."""

    def test_ignored_dirs_excluded_from_recursive_listing(self, workspace_tree):
        """No file from any ignored directory should appear in recursive output."""
        output = list_files.invoke({"path": ".", "recursive": True})
        paths = _listing_paths(output)
        for p in paths:
            assert not _contains_ignored_dir(p), (
                f"Ignored directory appeared in recursive listing: {p}"
            )

    def test_non_ignored_dirs_included(self, workspace_tree):
        """Files from non-ignored directories must still be present."""
        output = list_files.invoke({"path": ".", "recursive": True})
        paths = _listing_paths(output)
        assert "normal_file.txt" in paths
        assert any(p == "regular_dir/file_a.txt" for p in paths), (
            f"regular_dir/file_a.txt not found in paths: {paths}"
        )
        assert any(p == "sub/file_b.txt" for p in paths), (
            f"sub/file_b.txt not found in paths: {paths}"
        )
        assert any(p == "sub/deeper/file_c.txt" for p in paths), (
            f"sub/deeper/file_c.txt not found in paths: {paths}"
        )

    def test_nested_ignored_dir_excluded(self, workspace_tree):
        """A .git directory nested inside a non-ignored dir must also be pruned."""
        output = list_files.invoke({"path": ".", "recursive": True})
        paths = _listing_paths(output)
        for p in paths:
            assert "nested_ignore" not in Path(p).parts, (
                f"Nested ignored directory appeared in listing: {p}"
            )
            assert not (
                "nested_ignore" in Path(p).parts
                and ".git" in Path(p).parts
            ), f"Nested .git dir appeared in listing: {p}"

    def test_flat_mode_does_not_filter(self, workspace_tree):
        """Flat (non-recursive) listing must show ignored directory names as entries.

        IGNORE_DIRS filtering only applies to recursive os.walk traversal,
        not to a top-level os.listdir() call.
        """
        output = list_files.invoke({"path": ".", "recursive": False})
        lines = set(output.strip().split("\n"))
        assert ".git" in lines, "Flat listing should show .git entry"
        assert "node_modules" in lines, "Flat listing should show node_modules entry"
        assert "__pycache__" in lines, "Flat listing should show __pycache__ entry"
        assert "venv" in lines, "Flat listing should show venv entry"
        assert ".venv" in lines, "Flat listing should show .venv entry"
        assert "dist" in lines, "Flat listing should show dist entry"
        assert "build" in lines, "Flat listing should show build entry"


# ---------------------------------------------------------------------------
# 3. search_files
# ---------------------------------------------------------------------------

class TestSearchFiles:
    """Tests that IGNORE_DIRS filtering works in search_files."""

    def test_search_skips_ignored_dirs(self, workspace_tree):
        """Search results must not include files from ignored directories."""
        output = search_files.invoke({"query": "findme", "path": "."})
        paths = _search_result_paths(output)
        for p in paths:
            assert not _contains_ignored_dir(p), (
                f"Search result from ignored directory: {p}"
            )
        # Verify the expected non-ignored matches are present
        assert "normal_file.txt" in paths
        assert any(p == "sub/file_b.txt" for p in paths), (
            f"sub/file_b.txt not found in search results: {paths}"
        )
        assert any(p == "sub/deeper/file_c.txt" for p in paths), (
            f"sub/deeper/file_c.txt not found in search results: {paths}"
        )

    def test_search_does_not_traverse_into_ignored_dirs(self, workspace_tree):
        """A search term that exists only inside an ignored dir must find nothing."""
        output = search_files.invoke({"query": "git_config_content", "path": "."})
        assert output == "No matches found.", (
            f"Expected 'No matches found.' but got: {output}"
        )

    def test_search_results_truncation_still_respects_ignore_dirs(
        self, workspace_tree
    ):
        """A configured result limit still excludes ignored directories."""
        # Create 32 non-ignored files with a unique search term
        for i in range(32):
            d = workspace_tree / f"trunc_dir_{i}"
            d.mkdir(exist_ok=True)
            (d / "truncme.txt").write_text("truncme content")

        # Also place the same term inside ignored directories
        (workspace_tree / ".git" / "truncme.txt").write_text("truncme content")
        (workspace_tree / "node_modules" / "truncme.txt").write_text("truncme content")
        (workspace_tree / "dist" / "truncme.txt").write_text("truncme content")

        limited_search = minutus_mod.create_search_files_tool(30)
        output = limited_search.invoke({"query": "truncme", "path": "."})
        assert "Truncated" in output, (
            f"Expected truncation message in output: {output}"
        )
        paths = _search_result_paths(output)
        assert len(paths) == 30, (
            f"Expected exactly 30 truncated results, got {len(paths)}: {output}"
        )
        for p in paths:
            assert not _contains_ignored_dir(p), (
                f"Truncated search result from ignored directory: {p}"
            )


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------

class TestIgnoreDirsEdgeCases:
    """Tests for edge cases in IGNORE_DIRS filtering."""

    def test_case_sensitivity(self, workspace_tree):
        """Directory named NODE_MODULES (uppercase) must NOT be filtered.

        The filter uses exact set membership (d not in IGNORE_DIRS),
        which is case-sensitive.
        """
        output = list_files.invoke({"path": ".", "recursive": True})
        paths = _listing_paths(output)
        upper_paths = [p for p in paths if "NODE_MODULES" in Path(p).parts]
        assert len(upper_paths) > 0, (
            "NODE_MODULES (uppercase) should appear in recursive listing"
        )
        assert any(p == "NODE_MODULES/upper.txt" for p in paths), (
            f"NODE_MODULES/upper.txt not found in paths: {paths}"
        )

    def test_partial_name_not_matched(self, workspace_tree):
        """Directory named .gitter must NOT be filtered.

        The filter uses exact string equality, not substring matching,
        so .gitter is not caught by the .git entry in IGNORE_DIRS.
        """
        output = list_files.invoke({"path": ".", "recursive": True})
        paths = _listing_paths(output)
        gitter_paths = [p for p in paths if ".gitter" in Path(p).parts]
        assert len(gitter_paths) > 0, (
            ".gitter should appear in recursive listing"
        )
        assert any(p == ".gitter/sub_file.txt" for p in paths), (
            f".gitter/sub_file.txt not found in paths: {paths}"
        )

    def test_ignored_dir_as_explicit_path(self, workspace_tree):
        """Passing an ignored directory as the explicit target path should work.

        The dirs[:] pruning only filters subdirectories discovered during the
        walk — it does not reject the starting directory itself.
        """
        output = list_files.invoke({"path": ".git", "recursive": True})
        paths = _listing_paths(output)
        # Should list the contents of .git (relative to .git itself)
        assert "config" in paths, (
            f"Expected 'config' in listing of .git, got: {paths}"
        )
