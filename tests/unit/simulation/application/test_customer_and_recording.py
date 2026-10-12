"""The simulated customer and the recorder: briefs in, natural-shaped traces out."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

import pytest

from pac.guardrail.application.ports import BackendError, LLMRequest
from pac.simulation.application.agent import AgentError, SimulatedAgent
from pac.simulation.application.customer import (
    CustomerBrief,
    SimulatedCustomer,
    parse_customer_action,
    render_chat,
)
from pac.simulation.application.recording import asks_to_verify, record_conversation
from pac.simulation.domain.back_office import BackOffice
from tests.support.fakes import FakeBackend

EMAILS = {"C-1001": "ana.ruiz@mail.example"}
BRIEF = CustomerBrief(id="t", brief="You are Ana. Ask where LK-50211 is.", verify_as="C-1001")


def _scripted(*answers: Mapping[str, object]) -> FakeBackend:
    it: Iterator[str] = iter(json.dumps(a) for a in answers)
    return FakeBackend(lambda _: next(it))


def test_parse_customer_action() -> None:
    assert parse_customer_action('{"action": "say", "content": " hi "}') == "hi"
    assert parse_customer_action('<think>x</think>{"action": "end"}') is None
    with pytest.raises(ValueError, match="content"):
        parse_customer_action('{"action": "say"}')
    with pytest.raises(ValueError, match="JSON"):
        parse_customer_action("hello")


def test_chat_shows_only_what_a_chat_window_shows() -> None:
    assert "first message" in render_chat([])
    chat = render_chat([("customer", "hi"), ("assistant", "hello"), ("platform", "verified")])
    assert "YOU: hi" in chat
    assert "SUPPORT: hello" in chat
    assert "CHAT WINDOW: verified" in chat


async def test_customer_retries_then_gives_up() -> None:
    customer = SimulatedCustomer(FakeBackend(lambda _: "nope"))
    with pytest.raises(AgentError, match="no valid action"):
        await customer.next_message(BRIEF, [])

    def fail(_: LLMRequest) -> Exception:
        return BackendError("down")

    with pytest.raises(AgentError, match="down"):
        await SimulatedCustomer(FakeBackend(fail)).next_message(BRIEF, [])


def test_verification_hints() -> None:
    assert asks_to_verify("Please verify your identity with the code we sent.")
    assert not asks_to_verify("Your order shipped yesterday.")


def test_a_request_to_verify_is_noticed_in_the_briefs_languages() -> None:
    assert asks_to_verify("Por favor, verifica tu cuenta con el botón.")
    assert asks_to_verify("Pouvez-vous vérifier votre identité avec le bouton ?")
    assert asks_to_verify("يرجى استخدام زر التحقق في نافذة الدردشة لإثبات هويتك.")


async def test_a_recorded_conversation_verifies_like_the_platform() -> None:
    customer = SimulatedCustomer(
        _scripted(
            {"action": "say", "content": "where is LK-50211??"},
            {"action": "say", "content": "ok done"},
            {"action": "end"},
        )
    )
    agent = SimulatedAgent(
        _scripted(
            {
                "action": "tool_call",
                "tool_name": "lookup_order",
                "arguments": {"order_id": "LK-50211"},
            },
            {"action": "reply", "content": "Please verify with the code we sent to your email."},
            {"action": "reply", "content": "Thanks! It was delivered 12 days ago."},
        ),
        "You are Lark.",
    )
    recording = await record_conversation(BRIEF, customer, agent, BackOffice.seeded(), EMAILS)

    kinds = [e.kind for _, e in recording.events]
    assert kinds == [
        "user_input",
        "tool_call",
        "tool_response",
        "model_output",
        "user_input",
        "model_output",
    ]
    sessions = [s.verified for s, _ in recording.events]
    assert sessions == [False, False, False, False, True, True]
    assert recording.stopped is None


async def test_recording_stops_when_the_agent_never_replies() -> None:
    lookup = {
        "action": "tool_call",
        "tool_name": "lookup_order",
        "arguments": {"order_id": "LK-50211"},
    }
    customer = SimulatedCustomer(_scripted({"action": "say", "content": "hi"}))
    agent = SimulatedAgent(_scripted(*[lookup] * 2), "You are Lark.")
    recording = await record_conversation(
        BRIEF, customer, agent, BackOffice.seeded(), EMAILS, max_actions=2
    )
    assert recording.stopped is not None
    assert "2 actions" in recording.stopped


async def test_an_agent_failure_ends_the_recording_but_keeps_it() -> None:
    customer = SimulatedCustomer(_scripted({"action": "say", "content": "get me a human"}))
    agent = SimulatedAgent(
        FakeBackend(lambda _: '{"action": "escalate_to_human"}'), "You are Lark."
    )
    recording = await record_conversation(BRIEF, customer, agent, BackOffice.seeded(), EMAILS)
    assert [e.kind for _, e in recording.events] == ["user_input"]
    assert recording.stopped is not None
    assert recording.stopped.startswith("Lark failed")
