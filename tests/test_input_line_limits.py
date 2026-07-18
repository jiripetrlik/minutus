import subprocess

import pytest

import minutus.minutus as minutus

pytestmark = pytest.mark.unit


def test_truncate_first_lines():
    result = minutus.truncate_first_lines("one\ntwo\nthree", 2)
    assert result == "one\ntwo\n... Truncated: showing first 2 of 3 lines."


def test_truncate_last_lines():
    result = minutus.truncate_last_lines("one\ntwo\nthree", 2)
    assert result == "... Truncated: showing last 2 of 3 lines.\ntwo\nthree"


def test_file_context_uses_configured_limit(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("one\ntwo\nthree")
    result = minutus.load_files_context([str(path)], 2)
    assert "one\ntwo" in result
    assert "three" not in result
    assert "showing first 2 of 3 lines" in result


def test_clipboard_uses_configured_limit(monkeypatch):
    monkeypatch.setattr(minutus.pyperclip, "paste", lambda: "one\ntwo\nthree")
    result = minutus.load_clipboard_context(2)
    assert "one\ntwo" in result
    assert "three" not in result


def test_shell_tool_keeps_last_output_lines(monkeypatch):
    completed = subprocess.CompletedProcess(
        args="cmd", returncode=1, stdout="one\ntwo\nthree\n", stderr=""
    )
    monkeypatch.setattr(minutus.subprocess, "run", lambda *a, **kw: completed)
    tool = minutus.create_shell_command_tool(max_input_lines=2)
    result = tool.invoke({"command": "cmd"})
    assert result.startswith("Command failed with exit code 1.")
    assert "showing last 2 of 4 lines" in result
    assert result.endswith("two\nthree")


def test_workspace_tools_use_configured_limit(trusted_root):
    (trusted_root / "data.txt").write_text("match\nmatch\nmatch\n")
    read = minutus.create_read_file_tool(2)
    search = minutus.create_search_files_tool(2)
    assert "3: match" not in read.invoke({"path": "data.txt"})
    search_result = search.invoke({"query": "match", "path": "."})
    assert len([line for line in search_result.splitlines() if ": match" in line]) == 2
    assert "Truncated" in search_result


def test_invalid_limit_is_rejected(capsys):
    with pytest.raises(minutus.typer.Exit):
        minutus.validate_max_input_lines(0)
    assert "must be a positive integer" in capsys.readouterr().err


def test_url_tool_uses_configured_limit(monkeypatch):
    class Response:
        headers = {"Content-Type": "text/plain"}

        def read(self):
            return b"one\ntwo\nthree"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(minutus, "urlopen", lambda *args, **kwargs: Response())
    result = minutus.create_read_url_tool(2).invoke({"url": "https://example.com"})
    assert result == "one\ntwo\n... Truncated: showing first 2 of 3 lines."


def test_list_files_uses_configured_limit(trusted_root):
    for name in ("one", "two", "three"):
        (trusted_root / name).write_text("")
    result = minutus.create_list_files_tool(2).invoke({"path": "."})
    assert len([line for line in result.splitlines() if not line.startswith("... ")]) == 2
    assert "showing first 2 of 3 lines" in result


def test_default_limit_is_5000():
    assert minutus.DEFAULT_MAX_INPUT_LINES == 5000
