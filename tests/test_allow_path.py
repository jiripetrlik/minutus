"""
Unit tests for --allow-path: permitting workspace tools to reach extra trees.

These tests verify that:
  - With no allowed roots (the default) behavior is exactly the historical,
    strict containment: every escape is still rejected.
  - An allowed root is readable and writable by the workspace tools.
  - Paths outside every permitted root are still rejected, including siblings
    of an allowed root and ``..`` climbs out of one.
  - A symlink into an allowed root is followed; a symlink to any unlisted
    outside directory is still refused, by every tool, whether the link sits in
    the workspace or nested inside the allowed root itself.
  - ``get_safe_path`` returns a link-free absolute path, so the permissive mode
    cannot be redirected by retargeting a symlink after validation.
  - Ignore rules do not govern allowed trees.
  - ``resolve_allowed_paths`` validates and normalizes the CLI values.
  - ``--allow-path`` without an enabled workspace tool is a hard error.

All tests are pure unit tests — no API calls, no network, no subprocess.
"""

import os
import sys
from pathlib import Path

import pytest
import typer

# Ensure src/ is on sys.path so we can import the minutus package
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus.minutus import (
    create_append_file_tool,
    create_delete_path_tool,
    create_edit_file_tool,
    create_list_files_tool,
    create_read_file_tool,
    create_search_files_tool,
    create_write_file_tool,
    display_path,
    get_safe_path,
    is_path_permitted,
    resolve_allowed_paths,
    search_files,
    validate_allow_path_requires_workspace_tools,
)

pytestmark = pytest.mark.unit


symlink_support = pytest.mark.skipif(
    not hasattr(os, "symlink") or os.name == "nt",
    reason="Symlink support is required for these tests",
)


@pytest.fixture
def outside_dir(tmp_path):
    """A directory tree outside the trusted root, with one file in it.

    Returned fully resolved, because ``get_safe_path`` resolves candidates and
    the delete guard compares against resolved roots.

    Keyed on the unique ``tmp_path`` name because ``tmp_path.parent`` is shared
    by every test in the session. Without that, directories and symlinks created
    by one test would collide with the next one and raise ``FileExistsError``.
    """
    outside = (
        tmp_path.parent / f"{tmp_path.name}-outside" / "allowed_root"
    ).resolve()
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "external.txt").write_text("external content\n")
    return outside


def _tool_case(factory, make_args):
    """Pair a tool factory with an argument builder for a target directory.

    ``make_args`` receives the directory under test at call time, so the
    parametrization itself can stay module-level constant data (pytest needs an
    iterable at collection time, not a function to call later).
    """
    return factory, make_args


# One entry per built-in workspace tool. ``argvalues`` must be a constant
# iterable, so the arguments are built lazily inside the test instead of here.
_TOOL_CASES = {
    "read_file": _tool_case(
        create_read_file_tool, lambda root: {"path": str(root / "f.txt")}
    ),
    "write_file": _tool_case(
        create_write_file_tool,
        lambda root: {"path": str(root / "f.txt"), "content": "escaped"},
    ),
    "edit_file": _tool_case(
        create_edit_file_tool,
        lambda root: {
            "path": str(root / "f.txt"),
            "old_string": "a",
            "new_string": "b",
        },
    ),
    "append_file": _tool_case(
        create_append_file_tool,
        lambda root: {"path": str(root / "f.txt"), "content": "escaped"},
    ),
    "delete_path": _tool_case(
        create_delete_path_tool, lambda root: {"path": str(root / "f.txt")}
    ),
    "list_files": _tool_case(
        create_list_files_tool, lambda root: {"path": str(root)}
    ),
    "search_files": _tool_case(
        create_search_files_tool, lambda root: {"query": "a", "path": str(root)}
    ),
}


# ---------------------------------------------------------------------------
# Default behavior is unchanged: no allowed roots means strict containment
# ---------------------------------------------------------------------------

class TestDefaultStaysStrict:
    """With an empty allow-list, every historical guarantee still holds."""

    def test_get_safe_path_rejects_outside(self, trusted_root, outside_dir):
        with pytest.raises(ValueError) as exc_info:
            get_safe_path(str(outside_dir))
        assert str(exc_info.value) == (
            f"Security Error: Path '{outside_dir}' resolves outside the "
            "trusted workspace."
        )

    def test_empty_tuple_matches_legacy_check(self, trusted_root, outside_dir):
        assert is_path_permitted(trusted_root / "x.txt") is True
        assert is_path_permitted(outside_dir) is False
        assert is_path_permitted(outside_dir, ()) is False

    @pytest.mark.parametrize(
        "factory,make_args",
        list(_TOOL_CASES.values()),
        ids=list(_TOOL_CASES.keys()),
    )
    def test_every_tool_refuses_outside_without_allow_path(
        self, trusted_root, outside_dir, factory, make_args
    ):
        (outside_dir / "f.txt").write_text("original\n")
        # No allowed_roots: the default position must refuse every escape.
        tool = factory()
        result = tool.invoke(make_args(outside_dir))
        assert "Security Error" in result
        # The tree must be untouched, not merely reported as an error.
        assert (outside_dir / "f.txt").read_text() == "original\n"

    def test_in_workspace_paths_unchanged(self, trusted_root):
        (trusted_root / "a.txt").write_text("hello\n")
        read = create_read_file_tool()
        assert read.invoke({"path": "a.txt"}) == "1: hello"


# ---------------------------------------------------------------------------
# Containment union: workspace, then allowed roots
# ---------------------------------------------------------------------------

class TestAllowedRoots:
    """Allowed roots widen containment without widening it anywhere else."""

    def test_allowed_root_is_permitted(self, trusted_root, outside_dir):
        roots = (outside_dir,)
        result = get_safe_path(str(outside_dir), roots)
        assert result == outside_dir.resolve()

    def test_nested_subdirectory_is_permitted(self, trusted_root, outside_dir):
        nested = outside_dir / "sub" / "deeper"
        nested.mkdir(parents=True)
        roots = (outside_dir,)
        assert get_safe_path(str(nested), roots) == nested.resolve()
        assert get_safe_path(str(nested / "f.txt"), roots) == (
            nested / "f.txt"
        ).resolve()

    def test_sibling_of_allowed_root_is_rejected(self, trusted_root, outside_dir):
        sibling = outside_dir.parent / "not_allowed"
        sibling.mkdir(exist_ok=True)
        roots = (outside_dir,)
        with pytest.raises(ValueError) as exc_info:
            get_safe_path(str(sibling), roots)
        assert "Security Error" in str(exc_info.value)

    def test_parent_of_allowed_root_is_rejected(self, trusted_root, outside_dir):
        roots = (outside_dir,)
        with pytest.raises(ValueError) as exc_info:
            get_safe_path(str(outside_dir.parent), roots)
        assert "Security Error" in str(exc_info.value)

    def test_dotdot_climb_out_of_allowed_root_is_rejected(
        self, trusted_root, outside_dir
    ):
        (outside_dir / "sub").mkdir()
        roots = (outside_dir,)
        with pytest.raises(ValueError) as exc_info:
            get_safe_path(str(outside_dir / "sub" / ".." / ".."), roots)
        assert "Security Error" in str(exc_info.value)

    def test_workspace_is_still_permitted(self, trusted_root, outside_dir):
        roots = (outside_dir,)
        assert get_safe_path("a.txt", roots) == (trusted_root / "a.txt").resolve()


# ---------------------------------------------------------------------------
# Invariant: get_safe_path returns a link-free absolute path
# ---------------------------------------------------------------------------

class TestResolvedPathHasNoSymlinks:
    """``get_safe_path`` must return a path with no symlink components.

    Every tool validates with ``get_safe_path`` and then opens the returned
    path. Because that returned path is fully resolved and symlink-free, the
    operation cannot be redirected by retargeting a link after validation. This
    is what keeps the permissive ``--allow-path`` mode from becoming a
    time-of-check/time-of-use hole, so the property is pinned here.
    """

    @staticmethod
    def _symlink_components(path):
        """Return the symlink components of ``path``, if any.

        Inspects ``path`` and every ancestor up to the filesystem root, so an
        intermediate symlinked directory is caught as well as a final link.
        """
        found = []
        for candidate in [path, *path.parents]:
            if candidate.is_symlink():
                found.append(candidate)
        return found

    def test_in_workspace_path_has_no_symlinks(self, trusted_root):
        (trusted_root / "sub").mkdir()
        result = get_safe_path("sub/a.txt")
        assert self._symlink_components(result) == []
        assert result == (trusted_root / "sub" / "a.txt").resolve()

    def test_result_is_absolute(self, trusted_root):
        result = get_safe_path("a.txt")
        assert result.is_absolute()

    @symlink_support
    def test_contained_symlink_is_resolved_away(self, trusted_root):
        (trusted_root / "real.txt").write_text("data\n")
        os.symlink(trusted_root / "real.txt", trusted_root / "link.txt")
        result = get_safe_path("link.txt")
        assert result == (trusted_root / "real.txt").resolve()
        assert self._symlink_components(result) == []

    @symlink_support
    def test_symlinked_directory_is_resolved_away(self, trusted_root):
        (trusted_root / "real_dir").mkdir()
        (trusted_root / "real_dir" / "f.txt").write_text("x")
        os.symlink(trusted_root / "real_dir", trusted_root / "link_dir")
        result = get_safe_path("link_dir/f.txt")
        assert result == (trusted_root / "real_dir" / "f.txt").resolve()
        assert self._symlink_components(result) == []

    @symlink_support
    def test_allowed_root_reached_through_symlink_is_resolved_away(
        self, trusted_root, outside_dir
    ):
        os.symlink(outside_dir, trusted_root / "link")
        result = get_safe_path("link/external.txt", (outside_dir,))
        assert result == (outside_dir / "external.txt").resolve()
        assert self._symlink_components(result) == []

    def test_retargeting_a_link_cannot_redirect_the_resolved_path(
        self, trusted_root, outside_dir
    ):
        # The returned path is a concrete absolute path, so swapping the link
        # afterwards does not change what a caller would open.
        inner = trusted_root / "inner_target"
        inner.mkdir()
        (inner / "f.txt").write_text("inside")
        os.symlink(inner, trusted_root / "link")
        resolved = get_safe_path("link/f.txt")

        # Repoint the link at a different tree after validation.
        (trusted_root / "link").unlink()
        os.symlink(outside_dir, trusted_root / "link")

        assert resolved == (inner / "f.txt").resolve()
        assert resolved.read_text() == "inside"


# ---------------------------------------------------------------------------
# Symlinks into an allowed root are followed; others are not
# ---------------------------------------------------------------------------

class TestSymlinksWithAllowedRoots:
    """The allow-list is checked on the resolved target, as before."""

    @symlink_support
    def test_symlink_to_allowed_root_can_be_read(
        self, trusted_root, outside_dir
    ):
        os.symlink(outside_dir, trusted_root / "link")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert read.invoke({"path": "link/external.txt"}) == "1: external content"

    @symlink_support
    def test_symlink_to_allowed_root_can_be_written(
        self, trusted_root, outside_dir
    ):
        os.symlink(outside_dir, trusted_root / "link")
        write = create_write_file_tool(allowed_roots=(outside_dir,))
        result = write.invoke({"path": "link/via_link.txt", "content": "hi"})
        assert "Successfully wrote" in result
        assert (outside_dir / "via_link.txt").read_text() == "hi"

    @symlink_support
    def test_symlink_to_unlisted_outside_is_rejected(
        self, trusted_root, outside_dir
    ):
        other = outside_dir.parent / "unlisted"
        other.mkdir(exist_ok=True)
        os.symlink(other, trusted_root / "link")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        result = read.invoke({"path": "link/x.txt"})
        assert "Security Error" in result

    @symlink_support
    def test_contained_symlink_still_works_without_allow_path(
        self, trusted_root
    ):
        (trusted_root / "real.txt").write_text("data\n")
        os.symlink(trusted_root / "real.txt", trusted_root / "link.txt")
        read = create_read_file_tool()
        assert read.invoke({"path": "link.txt"}) == "1: data"


# ---------------------------------------------------------------------------
# An allowed root must not become a launchpad: links that leave it are refused
# ---------------------------------------------------------------------------

class TestSymlinkOutOfAllowedRoot:
    """A symlink pointing anywhere but a permitted root stays rejected.

    ``--allow-path`` permits a tree, not "anywhere a link happens to point".
    The containment check runs on the resolved target, so a link planted inside
    the workspace *or inside an allowed root* cannot escape to an unlisted
    directory.
    """

    @pytest.fixture
    def unlisted(self, outside_dir):
        """A directory outside the workspace and outside the allowed root."""
        target = (outside_dir.parent / "unlisted_target").resolve()
        target.mkdir(exist_ok=True)
        (target / "secret.txt").write_text("secret\n")
        return target

    # --- every tool must refuse the same escape ---------------------------

    @symlink_support
    def test_read_file_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert "Security Error" in read.invoke({"path": "link/secret.txt"})

    @symlink_support
    def test_write_file_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        write = create_write_file_tool(allowed_roots=(outside_dir,))
        result = write.invoke({"path": "link/new.txt", "content": "escaped"})
        assert "Security Error" in result
        assert not (unlisted / "new.txt").exists()

    @symlink_support
    def test_write_file_refuses_and_creates_no_directories(
        self, trusted_root, outside_dir, unlisted
    ):
        # write_file creates missing parents; that must not happen outside a
        # permitted root either.
        os.symlink(unlisted, trusted_root / "link")
        write = create_write_file_tool(allowed_roots=(outside_dir,))
        result = write.invoke({"path": "link/a/b/c.txt", "content": "x"})
        assert "Security Error" in result
        assert not (unlisted / "a").exists()

    @symlink_support
    def test_edit_file_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        edit = create_edit_file_tool(allowed_roots=(outside_dir,))
        result = edit.invoke(
            {"path": "link/secret.txt", "old_string": "secret",
             "new_string": "leaked"}
        )
        assert "Security Error" in result
        assert (unlisted / "secret.txt").read_text() == "secret\n"

    @symlink_support
    def test_append_file_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        append = create_append_file_tool(allowed_roots=(outside_dir,))
        result = append.invoke({"path": "link/secret.txt", "content": "x"})
        assert "Security Error" in result
        assert (unlisted / "secret.txt").read_text() == "secret\n"

    @symlink_support
    def test_delete_path_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        delete = create_delete_path_tool(allowed_roots=(outside_dir,))
        result = delete.invoke({"path": "link/secret.txt"})
        assert "Security Error" in result
        assert (unlisted / "secret.txt").exists()

    @symlink_support
    def test_list_files_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        list_tool = create_list_files_tool(allowed_roots=(outside_dir,))
        result = list_tool.invoke({"path": "link"})
        assert "Security Error" in result
        assert "secret.txt" not in result

    @symlink_support
    def test_search_files_refuses(self, trusted_root, outside_dir, unlisted):
        os.symlink(unlisted, trusted_root / "link")
        search = create_search_files_tool(allowed_roots=(outside_dir,))
        result = search.invoke({"query": "secret", "path": "link"})
        assert "Security Error" in result
        assert "No matches" not in result

    # --- the subtle one: a link nested inside the allowed root ------------

    @symlink_support
    def test_link_inside_allowed_root_cannot_escape_it(
        self, trusted_root, outside_dir, unlisted
    ):
        # allowed_root/sub/link -> unlisted. Reaching through it leaves the
        # permitted tree even though the request started inside it.
        sub = outside_dir / "sub"
        sub.mkdir(exist_ok=True)
        os.symlink(unlisted, sub / "link")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        result = read.invoke({"path": str(sub / "link" / "secret.txt")})
        assert "Security Error" in result

    @symlink_support
    def test_link_inside_allowed_root_cannot_write_out(
        self, trusted_root, outside_dir, unlisted
    ):
        sub = outside_dir / "sub"
        sub.mkdir(exist_ok=True)
        os.symlink(unlisted, sub / "link")
        write = create_write_file_tool(allowed_roots=(outside_dir,))
        result = write.invoke(
            {"path": str(sub / "link" / "out.txt"), "content": "x"}
        )
        assert "Security Error" in result
        assert not (unlisted / "out.txt").exists()

    @symlink_support
    def test_chained_links_out_of_allowed_root_are_refused(
        self, trusted_root, outside_dir, unlisted
    ):
        # hop1 (inside allowed root) -> hop2 (inside allowed root) -> unlisted
        os.symlink(unlisted, outside_dir / "hop2")
        os.symlink(outside_dir / "hop2", outside_dir / "hop1")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        result = read.invoke({"path": str(outside_dir / "hop1" / "secret.txt")})
        assert "Security Error" in result

    # --- and the permitted direction still works -------------------------

    @symlink_support
    def test_link_to_a_different_allowed_root_is_permitted(
        self, trusted_root, outside_dir
    ):
        # With two roots, a link in one pointing at the other is fine.
        second = (outside_dir.parent / "second_root").resolve()
        second.mkdir(exist_ok=True)
        (second / "ok.txt").write_text("ok\n")
        os.symlink(second, trusted_root / "link")
        read = create_read_file_tool(allowed_roots=(outside_dir, second))
        assert read.invoke({"path": "link/ok.txt"}) == "1: ok"


# ---------------------------------------------------------------------------
# Per-tool behavior on an allowed tree
# ---------------------------------------------------------------------------

class TestToolsOnAllowedRoot:
    """Each enabled tool actually reaches an allowed tree, end to end."""

    def test_list_files(self, trusted_root, outside_dir):
        list_tool = create_list_files_tool(allowed_roots=(outside_dir,))
        result = list_tool.invoke({"path": str(outside_dir)})
        assert "external.txt" in result

    def test_list_files_recursive(self, trusted_root, outside_dir):
        (outside_dir / "sub").mkdir()
        (outside_dir / "sub" / "nested.txt").write_text("x")
        list_tool = create_list_files_tool(allowed_roots=(outside_dir,))
        result = list_tool.invoke({"path": str(outside_dir), "recursive": True})
        assert "external.txt" in result
        assert os.path.join("sub", "nested.txt") in result

    def test_list_files_through_workspace_symlink(
        self, trusted_root, outside_dir
    ):
        if not hasattr(os, "symlink") or os.name == "nt":
            pytest.skip("Symlink support is required for this test")
        os.symlink(outside_dir, trusted_root / "link")
        list_tool = create_list_files_tool(allowed_roots=(outside_dir,))
        assert "external.txt" in list_tool.invoke({"path": "link"})

    def test_read_file(self, trusted_root, outside_dir):
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert read.invoke(
            {"path": str(outside_dir / "external.txt")}
        ) == "1: external content"

    def test_read_file_through_workspace_symlink(
        self, trusted_root, outside_dir
    ):
        if not hasattr(os, "symlink") or os.name == "nt":
            pytest.skip("Symlink support is required for this test")
        os.symlink(outside_dir, trusted_root / "link")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert read.invoke({"path": "link/external.txt"}) == "1: external content"

    def test_edit_file(self, trusted_root, outside_dir):
        edit = create_edit_file_tool(allowed_roots=(outside_dir,))
        result = edit.invoke(
            {"path": str(outside_dir / "external.txt"),
             "old_string": "external", "new_string": "edited"}
        )
        assert "Successfully edited" in result
        assert (outside_dir / "external.txt").read_text() == "edited content\n"

    def test_append_file(self, trusted_root, outside_dir):
        append = create_append_file_tool(allowed_roots=(outside_dir,))
        result = append.invoke(
            {"path": str(outside_dir / "external.txt"), "content": "appended"}
        )
        assert "Successfully appended" in result
        assert (outside_dir / "external.txt").read_text() == (
            "external content\nappended"
        )

    def test_search_files_reports_absolute_path(self, trusted_root, outside_dir):
        search = create_search_files_tool(allowed_roots=(outside_dir,))
        result = search.invoke(
            {"query": "external content", "path": str(outside_dir)}
        )
        assert result.startswith(str(outside_dir.resolve()) + os.sep)

    def test_search_files_through_workspace_symlink(
        self, trusted_root, outside_dir
    ):
        if not hasattr(os, "symlink") or os.name == "nt":
            pytest.skip("Symlink support is required for this test")
        os.symlink(outside_dir, trusted_root / "link")
        search = create_search_files_tool(allowed_roots=(outside_dir,))
        result = search.invoke({"query": "external content", "path": "link"})
        assert "external.txt" in result

    def test_search_files_in_workspace_stays_relative(self, trusted_root):
        (trusted_root / "a.txt").write_text("needle\n")
        result = search_files.invoke({"query": "needle", "path": "."})
        assert result.startswith("a.txt:1:")

    def test_write_file_creates_missing_subtree_inside_allowed_root(
        self, trusted_root, outside_dir
    ):
        write = create_write_file_tool(allowed_roots=(outside_dir,))
        target = outside_dir / "new" / "deep" / "f.txt"
        result = write.invoke({"path": str(target), "content": "x"})
        assert "Successfully wrote" in result
        assert target.read_text() == "x"

    def test_write_file_refuses_unlisted_outside(
        self, trusted_root, outside_dir
    ):
        other = outside_dir.parent / "unlisted_write"
        other.mkdir(exist_ok=True)
        write = create_write_file_tool(allowed_roots=(outside_dir,))
        result = write.invoke({"path": str(other / "f.txt"), "content": "x"})
        assert "Security Error" in result
        assert not (other / "f.txt").exists()

    def test_delete_path_removes_external_file(self, trusted_root, outside_dir):
        delete = create_delete_path_tool(allowed_roots=(outside_dir,))
        result = delete.invoke({"path": str(outside_dir / "external.txt")})
        assert "Successfully deleted file" in result
        assert not (outside_dir / "external.txt").exists()

    def test_delete_path_removes_external_empty_directory(
        self, trusted_root, outside_dir
    ):
        empty = outside_dir / "empty"
        empty.mkdir()
        delete = create_delete_path_tool(allowed_roots=(outside_dir,))
        result = delete.invoke({"path": str(empty)})
        assert "Successfully deleted empty directory" in result
        assert not empty.exists()

    def test_delete_path_refuses_non_empty_external_directory(
        self, trusted_root, outside_dir
    ):
        full = outside_dir / "full"
        full.mkdir()
        (full / "keep.txt").write_text("keep")
        delete = create_delete_path_tool(allowed_roots=(outside_dir,))
        result = delete.invoke({"path": str(full)})
        assert "not empty" in result
        assert (full / "keep.txt").exists()

    def test_delete_path_refuses_the_allowed_root_itself(
        self, trusted_root, outside_dir
    ):
        empty_root = (outside_dir.parent / "empty_allowed_root").resolve()
        empty_root.mkdir(exist_ok=True)
        delete = create_delete_path_tool(allowed_roots=(empty_root,))
        result = delete.invoke({"path": str(empty_root)})
        assert "Refusing to delete allowed root" in result
        assert empty_root.exists()

    def test_delete_path_refuses_the_allowed_root_via_symlink(
        self, trusted_root, outside_dir
    ):
        # Reaching the protected root through a link must not bypass the guard.
        if not hasattr(os, "symlink") or os.name == "nt":
            pytest.skip("Symlink support is required for this test")
        empty_root = (outside_dir.parent / "empty_allowed_root").resolve()
        empty_root.mkdir(exist_ok=True)
        os.symlink(empty_root, trusted_root / "link")
        delete = create_delete_path_tool(allowed_roots=(empty_root,))
        result = delete.invoke({"path": "link"})
        assert "Refusing to delete allowed root" in result
        assert empty_root.exists()

    def test_ignore_rules_do_not_govern_allowed_trees(
        self, trusted_root, outside_dir
    ):
        # The workspace ignore file excludes everything it can see.
        (trusted_root / ".aiignore").write_text("*\n")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert read.invoke({"path": str(outside_dir / "external.txt")}) == (
            "1: external content"
        )

    def test_allowed_root_ignore_file_is_not_consulted(
        self, trusted_root, outside_dir
    ):
        # An ignore file inside the allowed tree has no effect either: ignore
        # evaluation is anchored at the workspace root.
        (outside_dir / ".aiignore").write_text("*\n")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert read.invoke({"path": str(outside_dir / "external.txt")}) == (
            "1: external content"
        )

    def test_multiple_allowed_roots_are_all_reachable(
        self, trusted_root, outside_dir
    ):
        second = (outside_dir.parent / "second_root").resolve()
        second.mkdir(exist_ok=True)
        (second / "two.txt").write_text("two\n")
        read = create_read_file_tool(allowed_roots=(outside_dir, second))
        assert read.invoke({"path": str(outside_dir / "external.txt")}) == (
            "1: external content"
        )
        assert read.invoke({"path": str(second / "two.txt")}) == "1: two"

    def test_workspace_remains_reachable_alongside_allowed_root(
        self, trusted_root, outside_dir
    ):
        (trusted_root / "inside.txt").write_text("inside\n")
        read = create_read_file_tool(allowed_roots=(outside_dir,))
        assert read.invoke({"path": "inside.txt"}) == "1: inside"


# ---------------------------------------------------------------------------
# display_path
# ---------------------------------------------------------------------------

class TestDisplayPath:
    """In-workspace paths stay relative; external paths become absolute."""

    def test_in_workspace_is_relative(self, trusted_root):
        (trusted_root / "sub").mkdir()
        assert display_path(trusted_root / "sub" / "a.txt") == os.path.join(
            "sub", "a.txt"
        )

    def test_outside_is_absolute(self, trusted_root, outside_dir):
        assert display_path(outside_dir / "a.txt") == str(outside_dir / "a.txt")


# ---------------------------------------------------------------------------
# resolve_allowed_paths
# ---------------------------------------------------------------------------

class TestResolveAllowedPaths:
    """CLI values are validated up front and normalized to real paths."""

    def test_empty_yields_empty_tuple(self, trusted_root):
        assert resolve_allowed_paths([]) == ()

    def test_existing_directory_is_resolved(self, trusted_root, outside_dir):
        assert resolve_allowed_paths([str(outside_dir)]) == (outside_dir.resolve(),)

    def test_relative_value_resolves_against_workspace(self, trusted_root):
        (trusted_root / "sibling").mkdir()
        assert resolve_allowed_paths(["sibling"]) == (
            (trusted_root / "sibling").resolve(),
        )

    def test_duplicates_are_collapsed(self, trusted_root, outside_dir):
        roots = resolve_allowed_paths([str(outside_dir), str(outside_dir)])
        assert roots == (outside_dir.resolve(),)

    def test_missing_path_is_an_error(self, trusted_root, capsys):
        with pytest.raises(typer.Exit) as exit_info:
            resolve_allowed_paths(["/does/not/exist/at/all"])
        assert exit_info.value.exit_code != 0
        assert "does not exist" in capsys.readouterr().err

    def test_file_is_an_error(self, trusted_root, outside_dir, capsys):
        target = outside_dir / "external.txt"
        with pytest.raises(typer.Exit) as exit_info:
            resolve_allowed_paths([str(target)])
        assert exit_info.value.exit_code != 0
        assert "is not a directory" in capsys.readouterr().err

    def test_root_containing_workspace_is_an_error(self, trusted_root, capsys):
        # Allowing a parent of the workspace would make containment vacuous.
        with pytest.raises(typer.Exit) as exit_info:
            resolve_allowed_paths([str(trusted_root.parent)])
        assert exit_info.value.exit_code != 0
        err = capsys.readouterr().err
        assert "contains the workspace" in err

    def test_workspace_itself_is_an_error(self, trusted_root, capsys):
        with pytest.raises(typer.Exit):
            resolve_allowed_paths([str(trusted_root)])
        assert "contains the workspace" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# validate_allow_path_requires_workspace_tools
# ---------------------------------------------------------------------------

class TestAllowPathRequiresWorkspaceTools:
    """Supplying --allow-path with no workspace tool enabled is an error."""

    def test_no_workspace_tools_is_an_error(self, capsys):
        with pytest.raises(typer.Exit) as exit_info:
            validate_allow_path_requires_workspace_tools(
                ["/tmp/x"], read_only_workspace_tools=False,
                read_write_workspace_tools=False,
            )
        assert exit_info.value.exit_code != 0
        assert capsys.readouterr().err == (
            "Error: allow-path requires --read-only-workspace-tools or "
            "--read-write-workspace-tools\n"
        )

    def test_read_only_mode_allows_it(self):
        validate_allow_path_requires_workspace_tools(
            ["/tmp/x"],
            read_only_workspace_tools=True,
            read_write_workspace_tools=False,
        )

    def test_read_write_mode_allows_it(self):
        validate_allow_path_requires_workspace_tools(
            ["/tmp/x"],
            read_only_workspace_tools=False,
            read_write_workspace_tools=True,
        )

    def test_both_modes_flagged_allows_it(self):
        validate_allow_path_requires_workspace_tools(
            ["/tmp/x"],
            read_only_workspace_tools=True,
            read_write_workspace_tools=True,
        )

    def test_no_allow_path_needs_no_workspace_tools(self):
        # The default position must stay silent and untouched.
        validate_allow_path_requires_workspace_tools(
            [],
            read_only_workspace_tools=False,
            read_write_workspace_tools=False,
        )

    def test_empty_allow_path_is_not_an_error_in_any_mode(self):
        for read_only in (False, True):
            for read_write in (False, True):
                validate_allow_path_requires_workspace_tools(
                    [],
                    read_only_workspace_tools=read_only,
                    read_write_workspace_tools=read_write,
                )
