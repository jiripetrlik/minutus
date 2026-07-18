"""Unit tests for the CLI stdout/stderr stream contract."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from minutus.minutus import stream_response

pytestmark = pytest.mark.unit


async def async_values(values, error=None):
    for value in values:
        yield value
    if error is not None:
        raise error


class FakeMessage:
    def __init__(self, text=(), tool_calls=()):
        self.text = async_values(text)
        self.tool_calls = async_values(tool_calls)


class FakeStream:
    def __init__(self, messages, error=None):
        self.messages = async_values(messages, error)


class FakeAgent:
    def __init__(self, streams):
        self.streams = iter(streams)

    async def astream_events(self, agent_input, version, config):
        return next(self.streams)


def run(coro):
    return asyncio.run(coro)


def test_one_shot_writes_only_final_answer(capsys):
    agent = FakeAgent([FakeStream([FakeMessage(["final", " answer"])])])

    run(stream_response(agent, "question", {}, atomic_output=True))

    captured = capsys.readouterr()
    assert captured.out == "final answer\n"
    assert captured.err == ""


def test_failed_attempt_text_is_discarded(monkeypatch, capsys):
    agent = FakeAgent(
        [
            FakeStream([FakeMessage(["partial answer"])], RuntimeError("broken")),
            FakeStream([FakeMessage(["final answer"])]),
        ]
    )
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    run(stream_response(agent, "question", {}, atomic_output=True))

    captured = capsys.readouterr()
    assert captured.out == "final answer\n"
    assert "partial answer" not in captured.out
    assert "[Retry]" in captured.err
    assert "broken" in captured.err


def test_tool_cycle_is_diagnostic_and_final_cycle_is_result(monkeypatch, capsys):
    tool_call = {"index": 0, "name": "read_file", "args": {"path": "README.md"}}
    agent = FakeAgent(
        [
            FakeStream([FakeMessage(["I will inspect it."], [tool_call])]),
            FakeStream([FakeMessage(["The final answer."])]),
        ]
    )
    decision = AsyncMock(return_value={"type": "approve"})
    monkeypatch.setattr("minutus.minutus.decide_tool_call", decision)

    run(stream_response(agent, "question", {}, atomic_output=True))

    captured = capsys.readouterr()
    assert captured.out == "The final answer.\n"
    assert "I will inspect it." in captured.err
    assert "Tool call: read_file" in captured.err
    assert "README.md" in captured.err
    decision.assert_awaited_once()


def test_interactive_response_still_streams_directly(capsys):
    agent = FakeAgent([FakeStream([FakeMessage(["live", " text"])])])

    run(stream_response(agent, "question", {}, atomic_output=False))

    captured = capsys.readouterr()
    assert captured.out == "live text\n"
