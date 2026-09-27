"""Unit tests for tool execution progress diagnostics."""

from types import SimpleNamespace

import pytest

from minutus import minutus

pytestmark = pytest.mark.unit


def request(name="read_file", args=None):
    tool_call = {} if name is None else {"name": name}
    if args is not None:
        tool_call["args"] = args
    return SimpleNamespace(tool_call=tool_call)


def test_sync_tool_reports_before_execution_and_returns_result(monkeypatch):
    events = []
    expected = object()

    def diagnostic(text, **kwargs):
        events.append(("diagnostic", text, kwargs))

    def handler(tool_request):
        events.append(("handler", tool_request))
        return expected

    monkeypatch.setattr(minutus, "write_diagnostic", diagnostic)
    middleware = minutus.ToolRunningMiddleware()
    tool_request = request()

    result = middleware.wrap_tool_call(tool_request, handler)

    assert result is expected
    assert events == [
        ("diagnostic", "Running tool: read_file()", {"flush": True}),
        ("handler", tool_request),
    ]


@pytest.mark.asyncio
async def test_async_tool_reports_before_execution_and_returns_result(monkeypatch):
    events = []
    expected = object()

    def diagnostic(text, **kwargs):
        events.append(("diagnostic", text, kwargs))

    async def handler(tool_request):
        events.append(("handler", tool_request))
        return expected

    monkeypatch.setattr(minutus, "write_diagnostic", diagnostic)
    middleware = minutus.ToolRunningMiddleware()
    tool_request = request("mcp_lookup")

    result = await middleware.awrap_tool_call(tool_request, handler)

    assert result is expected
    assert events == [
        ("diagnostic", "Running tool: mcp_lookup()", {"flush": True}),
        ("handler", tool_request),
    ]


def test_running_message_is_written_only_to_stderr(capsys):
    middleware = minutus.ToolRunningMiddleware()
    middleware.wrap_tool_call(request("write_file"), lambda _: object())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Running tool: write_file()\n"


def test_missing_tool_name_uses_unknown(capsys):
    middleware = minutus.ToolRunningMiddleware()
    middleware.wrap_tool_call(request(None), lambda _: object())

    assert capsys.readouterr().err == "Running tool: unknown()\n"


def test_handler_error_is_propagated_after_running_message(capsys):
    middleware = minutus.ToolRunningMiddleware()

    def handler(_):
        raise RuntimeError("tool failed")

    with pytest.raises(RuntimeError, match="tool failed"):
        middleware.wrap_tool_call(request("failing_tool"), handler)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Running tool: failing_tool()\n"


def test_arguments_are_reported_compactly(capsys):
    middleware = minutus.ToolRunningMiddleware()
    middleware.wrap_tool_call(
        request("read_file", {"path": "src/minutus/minutus.py", "regex": True}),
        lambda _: object(),
    )

    assert capsys.readouterr().err == (
        'Running tool: read_file(path="src/minutus/minutus.py", regex=true)\n'
    )


def test_long_argument_values_are_truncated(capsys):
    middleware = minutus.ToolRunningMiddleware()
    middleware.wrap_tool_call(
        request("read_file", {"path": "z" * 60}),
        lambda _: object(),
    )

    assert capsys.readouterr().err == (
        'Running tool: read_file(path="' + "z" * 40 + '\u2026")\n'
    )
