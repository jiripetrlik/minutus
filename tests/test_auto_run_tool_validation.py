"""Unit tests for --auto-run-tools name validation."""

import pytest
import typer

from minutus import minutus

pytestmark = pytest.mark.unit


def test_known_enabled_tool_names_are_accepted(capsys):
    minutus.validate_auto_run_tools(
        ["read_file", "mcp_people_lookup"],
        ["list_files", "read_file", "mcp_people_lookup"],
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_unknown_names_fail_with_sorted_deduplicated_diagnostic(capsys):
    with pytest.raises(typer.Exit) as exit_info:
        minutus.validate_auto_run_tools(
            ["z_typo", "a_typo", "z_typo"],
            ["search_files", "list_files", "read_file"],
        )

    captured = capsys.readouterr()
    assert exit_info.value.exit_code != 0
    assert captured.out == ""
    assert captured.err == (
        "Error: unknown tool name(s) in --auto-run-tools: a_typo, z_typo. "
        "Available tools: list_files, read_file, search_files\n"
    )


def test_known_but_not_enabled_tool_is_unknown(capsys):
    with pytest.raises(typer.Exit):
        minutus.validate_auto_run_tools(["read_file"], [])

    assert capsys.readouterr().err == (
        "Error: unknown tool name(s) in --auto-run-tools: read_file. "
        "Available tools: (none)\n"
    )


def test_empty_auto_run_list_is_valid_when_no_tools_are_enabled(capsys):
    minutus.validate_auto_run_tools([], [])
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.asyncio
async def test_chat_rejects_unknown_name_before_model_or_agent_creation(
    monkeypatch, capsys
):
    def unexpected_creation(*args, **kwargs):
        pytest.fail("model or agent was created before auto-run validation")

    monkeypatch.setattr(minutus, "ChatOpenAI", unexpected_creation)
    monkeypatch.setattr(minutus, "create_agent", unexpected_creation)

    with pytest.raises(typer.Exit):
        await minutus.chat(
            model_name="unused",
            api_key=None,
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
            auto_run_tools=["read_flie"],
            auto_run_all_tools=True,
            non_interactive=False,
            temperature=None,
            structured_output=None,
            structured_output_strategy=minutus.StructuredOutputStrategy.provider,
            clipboard=False,
            max_input_lines=minutus.DEFAULT_MAX_INPUT_LINES,
            summarization_context_size=None,
            use_shell_command_tool=False,
            shell_command_timeout=30.0,
            use_read_url_tool=False,
            read_only_workspace_tools=True,
            read_write_workspace_tools=False,
            debug=False,
        )

    captured = capsys.readouterr()
    assert "unknown tool name(s)" in captured.err
    assert "read_flie" in captured.err
    assert "read_file" in captured.err


@pytest.mark.asyncio
async def test_print_tool_names_remains_discovery_only(monkeypatch, capsys):
    def unexpected_creation(*args, **kwargs):
        pytest.fail("discovery-only mode attempted to create a model or agent")

    monkeypatch.setattr(minutus, "ChatOpenAI", unexpected_creation)
    monkeypatch.setattr(minutus, "create_agent", unexpected_creation)

    await minutus.chat(
        model_name="unused",
        api_key=None,
        api_key_file=None,
        base_url=None,
        files=[],
        image=None,
        system_prompt="",
        prompt=None,
        prompt_file=None,
        system_prompt_file=None,
        mcp_config_json=None,
        print_tool_names=True,
        auto_run_tools=["ignored_in_discovery_mode"],
        auto_run_all_tools=False,
        non_interactive=False,
        temperature=None,
        structured_output=None,
        structured_output_strategy=minutus.StructuredOutputStrategy.provider,
        clipboard=False,
        max_input_lines=minutus.DEFAULT_MAX_INPUT_LINES,
        summarization_context_size=None,
        use_shell_command_tool=False,
        shell_command_timeout=30.0,
        use_read_url_tool=False,
        read_only_workspace_tools=True,
        read_write_workspace_tools=False,
        debug=False,
    )

    captured = capsys.readouterr()
    assert captured.out == "Available tools: list_files, read_file, search_files\n"
    assert captured.err == ""


@pytest.mark.asyncio
async def test_print_tool_names_read_write_includes_delete_path(monkeypatch, capsys):
    def unexpected_creation(*args, **kwargs):
        pytest.fail("discovery-only mode attempted to create a model or agent")

    monkeypatch.setattr(minutus, "ChatOpenAI", unexpected_creation)
    monkeypatch.setattr(minutus, "create_agent", unexpected_creation)

    await minutus.chat(
        model_name="unused",
        api_key=None,
        api_key_file=None,
        base_url=None,
        files=[],
        image=None,
        system_prompt="",
        prompt=None,
        prompt_file=None,
        system_prompt_file=None,
        mcp_config_json=None,
        print_tool_names=True,
        auto_run_tools=[],
        auto_run_all_tools=False,
        non_interactive=False,
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
        read_write_workspace_tools=True,
        debug=False,
    )

    captured = capsys.readouterr()
    assert captured.out == (
        "Available tools: list_files, read_file, search_files, write_file, "
        "edit_file, append_file, delete_path\n"
    )
    assert captured.err == ""
