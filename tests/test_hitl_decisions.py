"""Unit tests for interrupt-driven human-in-the-loop decision helpers.

These cover `extract_hitl_actions` and `build_resume_payload`, which derive
approval decisions from the graph's human-in-the-loop interrupt payload rather
than from streamed tool-call chunks.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.types import Interrupt

from minutus import minutus

pytestmark = pytest.mark.unit


def make_interrupt(action_requests, review_configs=None, interrupt_id="i1"):
    value = {"action_requests": action_requests}
    if review_configs is not None:
        value["review_configs"] = review_configs
    return Interrupt(value=value, id=interrupt_id)


# ---------------------------------------------------------------------------
# extract_hitl_actions
# ---------------------------------------------------------------------------

def test_extract_pairs_actions_with_review_configs_in_order():
    interrupt = make_interrupt(
        [{"name": "a"}, {"name": "b"}],
        review_configs=[
            {"action_name": "a", "allowed_decisions": ["approve", "reject"]},
            {"action_name": "b", "allowed_decisions": ["approve"]},
        ],
    )

    pairs = minutus.extract_hitl_actions([interrupt])

    assert [action["name"] for action, _ in pairs] == ["a", "b"]
    assert pairs[0][1]["allowed_decisions"] == ["approve", "reject"]
    assert pairs[1][1]["allowed_decisions"] == ["approve"]


def test_extract_defaults_missing_review_config_to_empty():
    interrupt = make_interrupt([{"name": "a"}])

    pairs = minutus.extract_hitl_actions([interrupt])

    assert len(pairs) == 1
    assert pairs[0][1] == {}


def test_extract_deduplicates_repeated_interrupt_ids():
    action = [{"name": "write_file"}]
    first = make_interrupt(action, interrupt_id="dup")
    second = make_interrupt(action, interrupt_id="dup")

    pairs = minutus.extract_hitl_actions([first, second])

    assert len(pairs) == 1


def test_extract_ignores_non_hitl_and_empty_interrupts():
    not_a_dict = SimpleNamespace(value="plain string", id="x")
    empty = make_interrupt([], interrupt_id="y")

    assert minutus.extract_hitl_actions([not_a_dict, empty]) == []


def test_extract_accepts_plain_values_without_id():
    pairs = minutus.extract_hitl_actions(
        [{"action_requests": [{"name": "c"}]}]
    )

    assert pairs == [({"name": "c"}, {})]


# ---------------------------------------------------------------------------
# build_resume_payload
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_resume_payload_returns_decisions_in_order(monkeypatch):
    interrupt = make_interrupt([{"name": "a"}, {"name": "b"}])
    decision = AsyncMock(side_effect=[{"type": "approve"}, {"type": "reject"}])
    monkeypatch.setattr(minutus, "decide_tool_call", decision)

    payload = await minutus.build_resume_payload([interrupt], non_interactive=False)

    assert payload == {"decisions": [{"type": "approve"}, {"type": "reject"}]}
    assert decision.await_count == 2


@pytest.mark.asyncio
async def test_build_resume_payload_returns_none_without_actions():
    payload = await minutus.build_resume_payload([], non_interactive=False)

    assert payload is None


@pytest.mark.asyncio
async def test_build_resume_payload_downgrades_decision_not_allowed(monkeypatch):
    interrupt = make_interrupt(
        [{"name": "a"}],
        review_configs=[{"action_name": "a", "allowed_decisions": ["reject"]}],
    )
    decision = AsyncMock(return_value={"type": "approve"})
    monkeypatch.setattr(minutus, "decide_tool_call", decision)

    payload = await minutus.build_resume_payload([interrupt], non_interactive=False)

    assert payload["decisions"][0]["type"] == "reject"