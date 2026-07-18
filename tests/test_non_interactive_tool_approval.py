"""Unit tests for non-interactive tool approval behavior."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from minutus import minutus

pytestmark = pytest.mark.unit


class FakeStdin:
    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self):
        return self._is_tty


@pytest.mark.asyncio
async def test_explicit_non_interactive_rejects_without_prompt(monkeypatch):
    monkeypatch.setattr(minutus.sys, "stdin", FakeStdin(True))

    async def fail(*args, **kwargs):
        pytest.fail("A prompt was opened in non-interactive mode")

    monkeypatch.setattr(minutus, "get_allow_reject_input", fail)
    monkeypatch.setattr(minutus, "get_reject_message", fail)

    decision = await minutus.decide_tool_call(
        {"name": "write_file", "args": {"path": "x", "content": "y"}},
        non_interactive=True,
    )

    assert decision["type"] == "reject"
    assert "Continue without it" in decision["message"]


@pytest.mark.asyncio
async def test_non_tty_rejects_without_prompt(monkeypatch):
    monkeypatch.setattr(minutus.sys, "stdin", FakeStdin(False))

    async def fail(*args, **kwargs):
        pytest.fail("A prompt was opened when stdin was not a TTY")

    monkeypatch.setattr(minutus, "get_allow_reject_input", fail)

    decision = await minutus.decide_tool_call(
        {"name": "read_file", "args": {"path": "README.md"}},
        non_interactive=False,
    )

    assert decision["type"] == "reject"


@pytest.mark.asyncio
async def test_tty_can_approve(monkeypatch):
    monkeypatch.setattr(minutus.sys, "stdin", FakeStdin(True))

    async def approve(*args, **kwargs):
        return True

    monkeypatch.setattr(minutus, "get_allow_reject_input", approve)

    decision = await minutus.decide_tool_call(
        {"name": "read_file", "args": {"path": "README.md"}},
        non_interactive=False,
    )

    assert decision == {"type": "approve"}


@pytest.mark.asyncio
async def test_eof_during_approval_rejects_without_reason_prompt(monkeypatch):
    monkeypatch.setattr(minutus.sys, "stdin", FakeStdin(True))

    async def eof(*args, **kwargs):
        raise EOFError

    async def fail(*args, **kwargs):
        pytest.fail("A rejection-reason prompt was opened after EOF")

    monkeypatch.setattr(minutus, "get_allow_reject_input", eof)
    monkeypatch.setattr(minutus, "get_reject_message", fail)

    decision = await minutus.decide_tool_call(
        {"name": "run_shell_command", "args": {"command": "pwd"}},
        non_interactive=False,
    )

    assert decision["type"] == "reject"
    assert decision["message"] == minutus.NON_INTERACTIVE_REJECTION_MESSAGE


class FakeInterruptAgent:
    def __init__(self):
        self.inputs = []

    async def ainvoke(self, agent_input, config):
        self.inputs.append(agent_input)
        if len(self.inputs) == 1:
            return {
                "__interrupt__": [
                    SimpleNamespace(
                        value={
                            "action_requests": [
                                {"name": "read_file", "args": {"path": "README.md"}}
                            ]
                        }
                    )
                ]
            }
        return {"structured_response": {"answer": "continued"}}


@pytest.mark.asyncio
async def test_invoke_resumes_agent_after_automatic_rejection(monkeypatch):
    monkeypatch.setattr(minutus.sys, "stdin", FakeStdin(False))
    agent = FakeInterruptAgent()

    result = await minutus.invoke_with_tool_approval(
        agent, {"messages": []}, runnable_config={}, non_interactive=False
    )

    assert result == {"structured_response": {"answer": "continued"}}
    assert len(agent.inputs) == 2
    resume = agent.inputs[1]
    assert resume.resume["decisions"][0]["type"] == "reject"
