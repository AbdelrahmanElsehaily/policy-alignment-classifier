"""A guarded turn: every event waits for the gatekeeper, and stopped events never move on."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence

from pac.guardrail.application.ports import LLMRequest
from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import Decision, Verdict
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.simulation.application.agent import SimulatedAgent
from pac.simulation.application.conversation import (
    Entry,
    EventGated,
    EventJudged,
    EventOccurred,
    GuardedConversation,
    TurnFinished,
    TurnStopped,
    TurnUpdate,
    agent_view,
    executed_calls,
    guardrail_view,
)
from pac.simulation.domain.back_office import BackOffice
from tests.support.fakes import FakeBackend

ANA = Session(verified=True, verified_customer_id="C-1001", email_on_file="ana.ruiz@mail.example")
REFUND = {
    "action": "tool_call",
    "tool_name": "issue_refund",
    "arguments": {"order_id": "LK-50211", "amount": 129.99, "reason": "defect"},
}
REPLY = {"action": "reply", "content": "Done."}


class ScriptedGuard:
    """Gives a fixed verdict per event kind (allow otherwise) and remembers what it was asked."""

    def __init__(
        self, verdicts: Mapping[str, Verdict] | None = None, *, fail: bool = False
    ) -> None:
        self._verdicts = dict(verdicts or {})
        self._fail = fail
        self.requests: list[ClassificationRequest] = []

    @property
    def name(self) -> str:
        return "scripted-guard"

    async def classify(self, request: ClassificationRequest) -> Classification:
        self.requests.append(request)
        await asyncio.sleep(0)
        if self._fail:
            raise RuntimeError("guard crashed")
        verdict = self._verdicts.get(request.event.kind, "allow")
        decision = Decision(
            rationale="test",
            verdict=verdict,
            rule_ids=("R5",) if verdict == "violation" else (),
            confidence=0.9,
        )
        return Classification(
            decision=decision,
            model=self.name,
            prompt_version="t",
            policy_version="v0",
            latency_ms=1.0,
        )


def _scripted(*actions: Mapping[str, object]) -> tuple[SimulatedAgent, FakeBackend]:
    answers = iter(json.dumps(a) for a in actions)
    responder: Callable[[LLMRequest], str] = lambda _: next(answers)  # noqa: E731
    backend = FakeBackend(responder)
    return SimulatedAgent(backend, "You are Lark."), backend


async def _run(
    conversation: GuardedConversation,
    history: Sequence[Entry] = (),
    message: str = "Refund LK-50211 please",
    *,
    office: BackOffice | None = None,
    sees_history: bool = True,
) -> list[TurnUpdate]:
    office = office or BackOffice.seeded()
    office.replay(executed_calls(history))
    stream = conversation.turn(office, ANA, history, message, guardrail_sees_history=sees_history)
    return [u async for u in stream]


def _occurred(updates: list[TurnUpdate]) -> list[EventOccurred]:
    return [u for u in updates if isinstance(u, EventOccurred)]


def _gates(updates: list[TurnUpdate]) -> dict[int, str]:
    return {u.index: u.gate for u in updates if isinstance(u, EventGated)}


def _refunded(office: BackOffice, order_id: str) -> float:
    lookup = TraceEvent(
        kind="tool_call", tool_name="lookup_order", arguments={"order_id": order_id}
    )
    content = office.execute(lookup).content
    assert content is not None
    refunded: float = json.loads(content)["refunded"]
    return refunded


async def test_allowed_events_pass_in_order_each_gated_before_the_next() -> None:
    agent, _ = _scripted(REFUND, REPLY)
    office = BackOffice.seeded()
    updates = await _run(GuardedConversation(agent, ScriptedGuard()), office=office)

    kinds = [u.event.kind for u in _occurred(updates)]
    assert kinds == ["user_input", "tool_call", "tool_response", "model_output"]
    assert set(_gates(updates).values()) == {"pass"}
    assert _refunded(office, "LK-50211") == 129.99
    # Each event is gated before the next one exists: the guardrail is on the path.
    position = {(type(u).__name__, getattr(u, "index", -1)): i for i, u in enumerate(updates)}
    for index in range(3):
        assert position[("EventGated", index)] < position[("EventOccurred", index + 1)]
    assert isinstance(updates[-1], TurnFinished)


async def test_a_blocked_tool_call_never_runs_and_the_agent_sees_the_error() -> None:
    agent, backend = _scripted(REFUND, REPLY)
    office = BackOffice.seeded()
    updates = await _run(
        GuardedConversation(agent, ScriptedGuard({"tool_call": "violation"})), office=office
    )

    occurred = _occurred(updates)
    assert [(u.event.kind, u.by_guardrail) for u in occurred] == [
        ("user_input", False),
        ("tool_call", False),
        ("tool_response", True),  # the guardrail's stand-in, not the back office's answer
        ("model_output", False),
    ]
    assert _gates(updates) == {0: "pass", 1: "block", 3: "pass"}
    assert "blocked by the policy guardrail (R5)" in (occurred[2].event.content or "")
    assert _refunded(office, "LK-50211") == 0
    assert "blocked by the policy guardrail" in backend.requests[1].user


async def test_a_held_tool_result_is_withheld_from_the_agent() -> None:
    agent, backend = _scripted(REFUND, REPLY)
    guard = ScriptedGuard({"tool_response": "policy_silent"})
    updates = await _run(GuardedConversation(agent, guard))

    assert _gates(updates)[2] == "hold"
    second_prompt = backend.requests[1].user
    assert '"status": "issued"' not in second_prompt
    assert "held for human review" in second_prompt


async def test_a_blocked_message_never_reaches_the_agent() -> None:
    agent, backend = _scripted(REPLY)
    updates = await _run(GuardedConversation(agent, ScriptedGuard({"user_input": "violation"})))

    occurred = _occurred(updates)
    assert [(u.event.kind, u.by_guardrail) for u in occurred] == [
        ("user_input", False),
        ("model_output", True),
    ]
    assert "Your message was blocked" in (occurred[1].event.content or "")
    assert backend.requests == []


async def test_a_blocked_reply_is_replaced_for_the_customer() -> None:
    agent, _ = _scripted(REPLY)
    guard = ScriptedGuard({"model_output": "violation"})
    occurred = _occurred(await _run(GuardedConversation(agent, guard)))
    assert occurred[-1].by_guardrail
    assert occurred[-1].event.content == "[This reply was blocked by the policy guardrail (R5).]"


async def test_a_crashing_gatekeeper_fails_closed_by_holding() -> None:
    agent, backend = _scripted(REPLY)
    updates = await _run(GuardedConversation(agent, ScriptedGuard(fail=True)))
    judged = [u for u in updates if isinstance(u, EventJudged)]
    assert judged[0].classification.error == "guard crashed"
    assert _gates(updates) == {0: "hold"}
    assert backend.requests == []


async def test_observers_judge_alongside_but_never_gate() -> None:
    agent, _ = _scripted(REPLY)
    baseline = ScriptedGuard({"user_input": "violation", "model_output": "violation"})
    updates = await _run(GuardedConversation(agent, ScriptedGuard(), [("baseline", baseline)]))
    engines = sorted((u.index, u.engine) for u in updates if isinstance(u, EventJudged))
    assert engines == [(0, "baseline"), (0, "llm"), (1, "baseline"), (1, "llm")]
    assert set(_gates(updates).values()) == {"pass"}


async def test_the_guardrail_sees_its_history_only_when_asked() -> None:
    history = [
        Entry(event=TraceEvent(kind="user_input", content="hi")),
        Entry(event=TraceEvent(kind="model_output", content="[blocked]"), by_guardrail=True),
    ]
    with_history, without = ScriptedGuard(), ScriptedGuard()
    await _run(GuardedConversation(_scripted(REPLY)[0], with_history), history)
    await _run(GuardedConversation(_scripted(REPLY)[0], without), history, sees_history=False)

    # The guardrail's own stand-ins are not part of what it has seen.
    assert [len(r.history) for r in with_history.requests] == [1, 2]
    assert all(r.history == () for r in without.requests)


def test_views_of_a_conversation_with_stopped_events() -> None:
    def ev(kind: str, text: str) -> TraceEvent:
        if kind == "tool_call":
            return TraceEvent(kind="tool_call", tool_name=text, arguments={})
        if kind == "tool_response":
            return TraceEvent(kind="tool_response", tool_name="t", content=text)
        return TraceEvent.model_validate({"kind": kind, "content": text})

    entries = [
        Entry(event=ev("user_input", "manipulative"), gate="block"),
        Entry(event=ev("model_output", "[notice to customer]"), by_guardrail=True),
        Entry(event=ev("user_input", "refund please")),
        Entry(event=ev("tool_call", "issue_refund"), gate="block"),
        Entry(event=ev("tool_response", "[error for agent]"), by_guardrail=True),
        Entry(event=ev("tool_call", "lookup_order")),
        Entry(event=ev("tool_response", "leak"), gate="block"),
        Entry(event=ev("model_output", "bad reply"), gate="block"),
    ]
    seen_by_agent = [e.content or e.tool_name for e in agent_view(entries)]
    assert seen_by_agent == [
        "refund please",
        "issue_refund",
        "[error for agent]",
        "lookup_order",
        "bad reply",  # the agent remembers what it said, even though the customer never saw it
    ]
    assert len(guardrail_view(entries)) == 6
    assert [e.tool_name for e in executed_calls(entries)] == ["lookup_order"]


async def test_a_turn_that_never_replies_is_cut_off() -> None:
    lookup = {
        "action": "tool_call",
        "tool_name": "lookup_order",
        "arguments": {"order_id": "LK-50211"},
    }
    agent, _ = _scripted(*[lookup] * 3)
    updates = await _run(GuardedConversation(agent, ScriptedGuard(), max_actions=3))
    assert len(_occurred(updates)) == 1 + 3 * 2
    assert any(isinstance(u, TurnStopped) and "3 actions" in u.reason for u in updates)


async def test_an_agent_failure_stops_the_turn() -> None:
    agent = SimulatedAgent(FakeBackend(lambda _: "not an action"), "You are Lark.")
    updates = await _run(GuardedConversation(agent, ScriptedGuard()))
    assert [u.event.kind for u in _occurred(updates)] == ["user_input"]
    stopped = [u for u in updates if isinstance(u, TurnStopped)]
    assert "simulated agent failed" in stopped[0].reason
    assert isinstance(updates[-1], TurnFinished)


async def test_closing_the_stream_early_cancels_the_turn() -> None:
    agent, _ = _scripted(REPLY)
    conversation = GuardedConversation(agent, ScriptedGuard(), [("baseline", ScriptedGuard())])
    stream = conversation.turn(BackOffice.seeded(), ANA, [], "hello")
    assert isinstance(await anext(stream), EventOccurred)
    await stream.aclose()  # must not hang or leak pending tasks
