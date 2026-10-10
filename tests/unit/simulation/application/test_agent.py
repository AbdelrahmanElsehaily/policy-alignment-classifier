"""The simulated agent: one JSON action per model call, parsed into a trace event."""

from __future__ import annotations

import json

import pytest

from pac.guardrail.application.ports import BackendError, LLMRequest
from pac.guardrail.domain.events import Session, TraceEvent
from pac.simulation.application.agent import (
    AgentError,
    SimulatedAgent,
    parse_action,
    render_transcript,
)
from tests.support.fakes import FakeBackend

ANA = Session(verified=True, verified_customer_id="C-1001", email_on_file="ana.ruiz@mail.example")


def test_parse_tool_call_and_reply() -> None:
    call = parse_action(
        '{"action": "tool_call", "tool_name": "lookup_order", "arguments": {"order_id": "LK-1"}}'
    )
    assert call == TraceEvent(
        kind="tool_call", tool_name="lookup_order", arguments={"order_id": "LK-1"}
    )
    reply = parse_action(
        '<think>hmm</think> Sure: {"action": "reply", "content": "Hello!"} trailing'
    )
    assert reply == TraceEvent(kind="model_output", content="Hello!")


def test_tool_call_without_arguments_gets_an_empty_object() -> None:
    call = parse_action('{"action": "tool_call", "tool_name": "escalate_to_human"}')
    assert call.arguments == {}


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        '{"action": "dance"}',
        '{"action": "tool_call"}',
        '{"action": "reply", "content": "   "}',
        '{"action": "reply"',
    ],
)
def test_parse_rejects_invalid_actions(text: str) -> None:
    with pytest.raises(ValueError, match=r"JSON|action|tool_name|content"):
        parse_action(text)


def test_transcript_shows_the_envelope_and_every_event_kind() -> None:
    transcript = [
        TraceEvent(kind="user_input", content="Where is LK-1?"),
        TraceEvent(kind="tool_call", tool_name="lookup_order", arguments={"order_id": "LK-1"}),
        TraceEvent(kind="tool_response", tool_name="lookup_order", content='{"status": "shipped"}'),
        TraceEvent(kind="model_output", content="It has shipped."),
    ]
    verified = render_transcript(ANA, transcript)
    assert "verified as customer C-1001" in verified
    assert 'YOU CALLED lookup_order {"order_id": "LK-1"}' in verified
    assert 'lookup_order RETURNED {"status": "shipped"}' in verified
    assert "YOU: It has shipped." in verified
    assert "NOT verified" in render_transcript(Session(), [])


async def test_next_event_retries_once_with_a_repair_note() -> None:
    answers = iter(["not json", json.dumps({"action": "reply", "content": "Hi!"})])
    backend = FakeBackend(lambda _: next(answers))
    agent = SimulatedAgent(backend, "You are Lark.")

    event = await agent.next_event(Session(), [TraceEvent(kind="user_input", content="hi")])

    assert event == TraceEvent(kind="model_output", content="Hi!")
    first, second = backend.requests
    assert first.system.startswith("You are Lark.")
    assert "How to answer" in first.system
    assert "not a valid action" in second.user
    assert "not a valid action" not in first.user
    assert agent.model == "fake-model"


async def test_next_event_gives_up_after_the_attempts() -> None:
    agent = SimulatedAgent(FakeBackend(lambda _: "nope"), "You are Lark.", max_attempts=2)
    with pytest.raises(AgentError, match="no valid action after 2 attempts"):
        await agent.next_event(Session(), [])


async def test_backend_failure_is_an_agent_error() -> None:
    def fail(_: LLMRequest) -> Exception:
        return BackendError("connection refused")

    with pytest.raises(AgentError, match="connection refused"):
        await SimulatedAgent(FakeBackend(fail), "You are Lark.").next_event(Session(), [])
