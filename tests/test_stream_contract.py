"""Unit tests for the CLI stdout/stderr stream contract."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Command, Interrupt

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
    def __init__(self, messages, error=None, interrupts=()):
        self.messages = async_values(messages, error)
        # Mirrors the `AsyncGraphRunStream` pause surface used by
        # `stream_response` to discover pending human-in-the-loop actions.
        self._interrupts = list(interrupts)
        self._interrupted = bool(interrupts)

    async def interrupted(self):
        return self._interrupted

    async def interrupts(self):
        return list(self._interrupts)


class FakeAgent:
    def __init__(self, streams):
        self.streams = iter(streams)
        self.inputs = []

    async def astream_events(self, agent_input, version, config):
        self.inputs.append(agent_input)
        return next(self.streams)


def run(coro):
    return asyncio.run(coro)


def hitl_interrupt(action_requests, allowed_decisions=None):
    """Build a human-in-the-loop interrupt shaped like the middleware's."""
    if allowed_decisions is None:
        allowed_decisions = ["approve", "edit", "reject", "respond"]
    return Interrupt(
        value={
            "action_requests": action_requests,
            "review_configs": [
                {
                    "action_name": action["name"],
                    "allowed_decisions": allowed_decisions,
                }
                for action in action_requests
            ],
        }
    )


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
    interrupt = hitl_interrupt(
        [{"name": "read_file", "args": {"path": "README.md"}}]
    )
    agent = FakeAgent(
        [
            FakeStream(
                [FakeMessage(["I will inspect it."])], interrupts=[interrupt]
            ),
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


class RecordingInterruptAgent:
    """Agent fake that records the decisions submitted on resume.

    The real `HumanInTheLoopMiddleware` raises `ValueError` ("Number of human
    decisions ... does not match number of hanging tool calls") when the resume
    payload has more decisions than there are gated actions in the interrupt
    batch. We record the submitted decisions so the test can assert the same
    invariant without depending on the middleware's message.
    """

    def __init__(self, interrupt_actions, streamed_tool_calls, final_message):
        self._interrupt = hitl_interrupt(interrupt_actions)
        self._streamed_tool_calls = streamed_tool_calls
        self._final_message = final_message
        self.inputs = []
        self.resume_decisions = []

    async def astream_events(self, agent_input, version, config):
        self.inputs.append(agent_input)
        if isinstance(agent_input, Command):
            self.resume_decisions = agent_input.resume["decisions"]
            return FakeStream([FakeMessage([self._final_message])])
        # First turn: the model proposes every tool it wants to run. Auto-run
        # tools show up here, but the middleware excludes them from the
        # interrupt batch below.
        return FakeStream(
            [FakeMessage(["Working on it."], self._streamed_tool_calls)],
            interrupts=[self._interrupt],
        )


def test_only_gated_tools_are_submitted_for_decision(monkeypatch, capsys):
    """A turn that mixes an auto-run tool with a gated one must submit exactly
    one decision: only the gated tool appears in the interrupt batch.
    """
    # The model proposed both tools, but `list_files` is auto-run, so the
    # middleware put only `write_file` in `action_requests`.
    streamed_tool_calls = [
        {"index": 0, "name": "list_files", "args": {"path": "."}},
        {
            "index": 1,
            "name": "write_file",
            "args": {"path": "example.txt", "content": "hi"},
        },
    ]
    interrupt_actions = [
        {"name": "write_file", "args": {"path": "example.txt", "content": "hi"}}
    ]
    agent = RecordingInterruptAgent(interrupt_actions, streamed_tool_calls, "Done.")
    decision = AsyncMock(return_value={"type": "approve"})
    monkeypatch.setattr("minutus.minutus.decide_tool_call", decision)

    run(stream_response(agent, "question", {}, atomic_output=True))

    # One decision per gated action, never per streamed tool call.
    assert agent.resume_decisions == [{"type": "approve"}]
    decision.assert_awaited_once()
    assert capsys.readouterr().out == "Done.\n"


def test_auto_approved_tool_does_not_prompt(monkeypatch, capsys):
    """Auto-run tools execute inside the run without any approval prompt."""
    # The model calls an auto-run tool, then produces its final answer. No
    # interrupt is raised, so no decision should be requested.
    auto_run_call = {"index": 0, "name": "list_files", "args": {"path": "."}}
    agent = FakeAgent(
        [
            FakeStream(
                [
                    FakeMessage(["Let me look."], [auto_run_call]),
                    FakeMessage(["The final answer."]),
                ]
            )
        ]
    )
    decision = AsyncMock(return_value={"type": "approve"})
    monkeypatch.setattr("minutus.minutus.decide_tool_call", decision)

    run(stream_response(agent, "question", {}, atomic_output=True))

    captured = capsys.readouterr()
    assert captured.out == "The final answer.\n"
    assert "Tool call:" not in captured.err
    decision.assert_not_awaited()


def test_interrupt_without_actions_raises(monkeypatch):
    """An interrupt that carries no gated actions is an error, not a silent stall."""
    empty_interrupt = Interrupt(
        value={"action_requests": [], "review_configs": []}
    )
    agent = FakeAgent([FakeStream([], interrupts=[empty_interrupt])])
    decision = AsyncMock(return_value={"type": "approve"})
    monkeypatch.setattr("minutus.minutus.decide_tool_call", decision)

    with pytest.raises(RuntimeError, match="contained no tool calls"):
        run(stream_response(agent, "question", {}, atomic_output=True))

    decision.assert_not_awaited()


def test_interactive_response_still_streams_directly(capsys):
    agent = FakeAgent([FakeStream([FakeMessage(["live", " text"])])])

    run(stream_response(agent, "question", {}, atomic_output=False))

    captured = capsys.readouterr()
    assert captured.out == "live text\n"
