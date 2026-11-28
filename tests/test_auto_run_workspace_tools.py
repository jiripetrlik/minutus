"""Unit tests for --auto-run-workspace-tools.

These tests verify that:
  - The option authorizes exactly the enabled built-in workspace tools: the
    read-only set in read-only mode, and the read-only plus read-write set in
    read-write mode (read-write still wins when both flags are supplied).
  - Shell, URL, and MCP tools stay gated, because their names are never part of
    the resolved workspace tool set.
  - Turning the option off leaves the existing gating behavior untouched.
  - ``--auto-run-workspace-tools`` without an enabled workspace tool is a hard
    error rather than silently ignored input.

All tests are pure unit tests - no API calls, no network, no subprocess.
"""

import sys
from pathlib import Path

import pytest
import typer

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus import minutus

pytestmark = pytest.mark.unit


def _chat_kwargs(**overrides):
    """Return the full ``chat()`` keyword set, overridable per test.

    Every option is passed explicitly so these tests do not depend on Typer's
    ``OptionInfo`` fallbacks.
    """
    kwargs = dict(
        model_name="unused",
        api_key="test-key",
        api_key_file=None,
        base_url=None,
        files=[],
        image=None,
        system_prompt="",
        prompt="hello",
        prompt_file=None,
        system_prompt_file=None,
        mcp_config_json=None,
        print_tool_names=False,
        auto_run_tools=[],
        auto_run_all_tools=False,
        auto_run_workspace_tools=False,
        non_interactive=True,
        temperature=None,
        structured_output=None,
        structured_output_strategy=minutus.StructuredOutputStrategy.provider,
        clipboard=False,
        max_input_lines=minutus.DEFAULT_MAX_INPUT_LINES,
        summarization_context_size=None,
        use_shell_command_tool=False,
        shell_command_timeout=30.0,
        use_read_url_tool=False,
        read_only_workspace_tools=False,
        read_write_workspace_tools=False,
        debug=False,
    )
    kwargs.update(overrides)
    return kwargs


def _install_recording_fakes(monkeypatch):
    """Replace the model, agent, and middleware with recording fakes.

    Returns the dict that receives the computed ``interrupt_on`` mapping, so a
    test can assert exactly which tools would prompt for approval.
    """
    recorded = {}

    class RecordingHITL:
        def __init__(self, interrupt_on):
            recorded["interrupt_on"] = interrupt_on

    class RecordingAgent:
        async def ainvoke(self, agent_input, config):
            return {"messages": []}

    async def fake_stream_response(*args, **kwargs):
        return None

    monkeypatch.setattr(minutus, "HumanInTheLoopMiddleware", RecordingHITL)
    monkeypatch.setattr(minutus, "ChatOpenAI", lambda **kwargs: object())
    monkeypatch.setattr(minutus, "create_agent", lambda **kwargs: RecordingAgent())
    monkeypatch.setattr(minutus, "stream_response", fake_stream_response)
    return recorded


# --- resolve_auto_run_workspace_tools -------------------------------------


def test_read_only_mode_authorizes_only_read_only_tool_names():
    names = minutus.resolve_auto_run_workspace_tools(
        True, read_only_workspace_tools=True, read_write_workspace_tools=False
    )

    assert names == ["list_files", "read_file", "search_files"]


def test_read_write_mode_authorizes_all_workspace_tool_names():
    names = minutus.resolve_auto_run_workspace_tools(
        True, read_only_workspace_tools=False, read_write_workspace_tools=True
    )

    assert names == [
        "list_files",
        "read_file",
        "search_files",
        "write_file",
        "edit_file",
        "append_file",
        "delete_path",
    ]


def test_read_write_mode_wins_when_both_workspace_flags_are_supplied():
    both = minutus.resolve_auto_run_workspace_tools(
        True, read_only_workspace_tools=True, read_write_workspace_tools=True
    )
    read_write_only = minutus.resolve_auto_run_workspace_tools(
        True, read_only_workspace_tools=False, read_write_workspace_tools=True
    )

    assert both == read_write_only


def test_disabled_option_authorizes_nothing():
    assert (
        minutus.resolve_auto_run_workspace_tools(
            False, read_only_workspace_tools=True, read_write_workspace_tools=True
        )
        == []
    )


def test_resolved_names_never_include_non_workspace_tools():
    """Shell, URL, and MCP tools must never be authorized by this option."""
    non_workspace = {"run_shell_command", "read_url", "get_age"}

    for read_only, read_write in [(True, False), (False, True), (True, True)]:
        names = set(
            minutus.resolve_auto_run_workspace_tools(
                True,
                read_only_workspace_tools=read_only,
                read_write_workspace_tools=read_write,
            )
        )
        assert names.isdisjoint(non_workspace)


def test_workspace_tool_name_constants_match_tool_registrations():
    """The constants must describe the tools the CLI actually registers."""
    read_only_tools = [
        minutus.create_list_files_tool(),
        minutus.create_read_file_tool(),
        minutus.create_search_files_tool(),
    ]
    read_write_tools = [
        minutus.create_write_file_tool(),
        minutus.create_edit_file_tool(),
        minutus.create_append_file_tool(),
        minutus.create_delete_path_tool(),
    ]

    assert tuple(tool.name for tool in read_only_tools) == (
        minutus.READ_ONLY_WORKSPACE_TOOL_NAMES
    )
    assert tuple(tool.name for tool in read_write_tools) == (
        minutus.READ_WRITE_WORKSPACE_TOOL_NAMES
    )


# --- validate_auto_run_workspace_tools_requires_workspace_tools -----------


def test_enabled_option_with_workspace_tools_is_accepted(capsys):
    minutus.validate_auto_run_workspace_tools_requires_workspace_tools(
        True, read_only_workspace_tools=True, read_write_workspace_tools=False
    )
    minutus.validate_auto_run_workspace_tools_requires_workspace_tools(
        True, read_only_workspace_tools=False, read_write_workspace_tools=True
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_enabled_option_without_workspace_tools_fails(capsys):
    with pytest.raises(typer.Exit) as exit_info:
        minutus.validate_auto_run_workspace_tools_requires_workspace_tools(
            True, read_only_workspace_tools=False, read_write_workspace_tools=False
        )

    captured = capsys.readouterr()
    assert exit_info.value.exit_code != 0
    assert captured.out == ""
    assert captured.err == (
        "Error: auto-run-workspace-tools requires --read-only-workspace-tools or "
        "--read-write-workspace-tools\n"
    )


def test_disabled_option_without_workspace_tools_is_accepted(capsys):
    minutus.validate_auto_run_workspace_tools_requires_workspace_tools(
        False, read_only_workspace_tools=False, read_write_workspace_tools=False
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


# --- chat() wiring --------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_rejects_option_without_workspace_tools(monkeypatch, capsys):
    def unexpected_creation(*args, **kwargs):
        pytest.fail("model or agent was created despite invalid auto-run option")

    monkeypatch.setattr(minutus, "ChatOpenAI", unexpected_creation)
    monkeypatch.setattr(minutus, "create_agent", unexpected_creation)

    with pytest.raises(typer.Exit):
        await minutus.chat(
            **_chat_kwargs(
                auto_run_workspace_tools=True,
                read_only_workspace_tools=False,
                read_write_workspace_tools=False,
            )
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "auto-run-workspace-tools requires" in captured.err


@pytest.mark.asyncio
async def test_chat_auto_runs_workspace_tools_in_read_only_mode(monkeypatch):
    """Read-only auto-run ungates workspace tools and leaves others gated."""
    recorded = _install_recording_fakes(monkeypatch)

    await minutus.chat(
        **_chat_kwargs(
            auto_run_workspace_tools=True,
            use_shell_command_tool=True,
            use_read_url_tool=True,
            read_only_workspace_tools=True,
        )
    )

    assert recorded["interrupt_on"] == {
        "run_shell_command": True,
        "read_url": True,
        "list_files": False,
        "read_file": False,
        "search_files": False,
    }


@pytest.mark.asyncio
async def test_chat_auto_runs_workspace_tools_in_read_write_mode(monkeypatch):
    """Read-write auto-run ungates read-write tools and leaves others gated."""
    recorded = _install_recording_fakes(monkeypatch)

    await minutus.chat(
        **_chat_kwargs(
            auto_run_workspace_tools=True,
            use_shell_command_tool=True,
            read_write_workspace_tools=True,
        )
    )

    assert recorded["interrupt_on"] == {
        "run_shell_command": True,
        "list_files": False,
        "read_file": False,
        "search_files": False,
        "write_file": False,
        "edit_file": False,
        "append_file": False,
        "delete_path": False,
    }


@pytest.mark.asyncio
async def test_chat_without_option_keeps_every_workspace_tool_gated(monkeypatch):
    """Omitting the option preserves the historical gating behavior."""
    recorded = _install_recording_fakes(monkeypatch)

    await minutus.chat(**_chat_kwargs(read_write_workspace_tools=True))

    assert recorded["interrupt_on"] == {
        "list_files": True,
        "read_file": True,
        "search_files": True,
        "write_file": True,
        "edit_file": True,
        "append_file": True,
        "delete_path": True,
    }


# --- validate_unique_tool_names -------------------------------------------


class _NamedTool:
    """Minimal stand-in for a tool object: only ``.name`` is inspected."""

    def __init__(self, name):
        self.name = name


def _workspace_tools():
    """Build real workspace tool objects, as ``chat()`` does in read-only mode."""
    return [
        minutus.create_list_files_tool(),
        minutus.create_read_file_tool(),
        minutus.create_search_files_tool(),
    ]


def test_unique_names_are_accepted(capsys):
    workspace = _workspace_tools()
    tools = [_NamedTool("run_shell_command"), _NamedTool("get_age"), *workspace]

    minutus.validate_unique_tool_names(tools, workspace)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_empty_tool_set_is_accepted(capsys):
    minutus.validate_unique_tool_names([], [])

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_workspace_name_reused_by_another_tool_fails(capsys):
    """The dangerous case: a non-workspace tool claims a workspace tool name."""
    workspace = _workspace_tools()
    # "read_file" really is in the read-only workspace set; the impostor is a
    # distinct object, so only identity distinguishes it from the real tool.
    impostor = _NamedTool("read_file")
    tools = [impostor, *workspace]

    with pytest.raises(typer.Exit) as exit_info:
        minutus.validate_unique_tool_names(tools, workspace)

    captured = capsys.readouterr()
    assert exit_info.value.exit_code != 0
    assert captured.out == ""
    assert "tool name collision detected" in captured.err
    assert "workspace tool name(s) also used by another enabled tool: read_file" in (
        captured.err
    )
    # A collision involving a workspace tool is exactly the case where an
    # auto-run flag could authorize the wrong tool, so the message says so.
    assert "auto-run option could allow the wrong tool" in captured.err


def test_duplicate_non_workspace_names_fail_generically(capsys):
    """Two third-party tools sharing a name are ambiguous too, but not as a
    workspace overlap."""
    workspace = _workspace_tools()
    tools = [_NamedTool("get_age"), _NamedTool("get_age"), *workspace]

    with pytest.raises(typer.Exit):
        minutus.validate_unique_tool_names(tools, workspace)

    captured = capsys.readouterr()
    assert "tool name collision detected" in captured.err
    assert "name(s) used by more than one enabled tool: get_age" in captured.err
    assert "workspace tool name(s) also used" not in captured.err


def test_both_collision_kinds_are_reported_together(capsys):
    workspace = _workspace_tools()
    tools = [
        _NamedTool("read_file"),
        _NamedTool("get_age"),
        _NamedTool("get_age"),
        *workspace,
    ]

    with pytest.raises(typer.Exit):
        minutus.validate_unique_tool_names(tools, workspace)

    captured = capsys.readouterr()
    assert "workspace tool name(s) also used by another enabled tool: read_file" in (
        captured.err
    )
    assert "name(s) used by more than one enabled tool: get_age" in captured.err


def test_workspace_identity_is_used_not_name(capsys):
    """A name alone does not make a tool a workspace tool.

    ``read_file`` *is* a workspace tool name, but the two enabled objects here
    are not the registered workspace objects (which are deliberately absent from
    ``tools``). Classification is by identity, so the message must be the
    generic one rather than the workspace-overlap one.
    """
    workspace = _workspace_tools()  # real objects, none of them enabled here
    tools = [_NamedTool("read_file"), _NamedTool("read_file")]

    with pytest.raises(typer.Exit):
        minutus.validate_unique_tool_names(tools, workspace)

    err = capsys.readouterr().err
    assert "name(s) used by more than one enabled tool: read_file" in err
    assert "workspace tool name(s) also used" not in err


def test_repeated_real_workspace_tool_is_a_collision(capsys):
    """Registering the same workspace tool twice is also ambiguous."""
    workspace = _workspace_tools()
    tools = [workspace[0], workspace[0], *workspace]

    with pytest.raises(typer.Exit):
        minutus.validate_unique_tool_names(tools, workspace)

    captured = capsys.readouterr()
    assert "workspace tool name(s) also used by another enabled tool: list_files" in (
        captured.err
    )


@pytest.mark.asyncio
async def test_chat_fails_fast_on_workspace_name_collision(monkeypatch, capsys):
    """A colliding tool set is rejected before any model or agent is created."""
    impostor = _NamedTool("read_file")

    def unexpected_creation(*args, **kwargs):
        pytest.fail("model or agent was created despite a tool name collision")

    monkeypatch.setattr(
        minutus, "create_shell_command_tool", lambda *a, **k: impostor
    )
    monkeypatch.setattr(minutus, "ChatOpenAI", unexpected_creation)
    monkeypatch.setattr(minutus, "create_agent", unexpected_creation)

    with pytest.raises(typer.Exit):
        await minutus.chat(
            **_chat_kwargs(
                use_shell_command_tool=True,
                read_only_workspace_tools=True,
            )
        )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "tool name collision detected" in captured.err
    assert "read_file" in captured.err


@pytest.mark.asyncio
async def test_chat_rejects_collision_even_without_auto_run(monkeypatch, capsys):
    """The guard is unconditional: an ambiguous set is never run, even when no
    auto-run option is supplied and every tool would be gated anyway."""
    impostor = _NamedTool("list_files")

    def unexpected_creation(*args, **kwargs):
        pytest.fail("model or agent was created despite a tool name collision")

    monkeypatch.setattr(
        minutus, "create_shell_command_tool", lambda *a, **k: impostor
    )
    monkeypatch.setattr(minutus, "ChatOpenAI", unexpected_creation)
    monkeypatch.setattr(minutus, "create_agent", unexpected_creation)

    with pytest.raises(typer.Exit):
        await minutus.chat(
            **_chat_kwargs(
                use_shell_command_tool=True,
                read_only_workspace_tools=True,
                auto_run_workspace_tools=False,
            )
        )

    assert "tool name collision detected" in capsys.readouterr().err
