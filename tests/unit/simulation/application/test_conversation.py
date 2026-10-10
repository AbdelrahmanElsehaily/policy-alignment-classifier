"""A turn streams every event with every observer's verdict, and never blocks the agent."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence

from pac.guardrail.application.ports import LLMRequest
from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import Decision
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.simulation.application.agent import SimulatedAgent
from pac.simulation.application.conversation import (
    EventJudged,
    EventOccurred,
    ObservedConversation,
    TurnFinished,
    TurnStopped,
    TurnUpdate,
)
from pac.simulation.domain.back_office import BackOffice
from tests.support.fakes import FakeBackend

ANA = Session(verified=True, verified_customer_id="C-1001", email_on_file="ana.ruiz@mail.example")


class RecordingClassifier:
    """Flags every tool call, allows everything else, and remembers what it was asked."""

    def __init__(self, name: str = "fake-guard", *, fail: bool = False) -> None:
        self._name = name
        self._fail = fail
        self.requests: list[ClassificationRequest] = []

    @property
    def name(self) -> str:
        return self._name

    async def classify(self, request: ClassificationRequest) -> Classification:
        self.requests.append(request)
        await asyncio.sleep(0)
        if self._fail:
            raise RuntimeError("guard crashed")
        flagged = request.event.kind == "tool_call"
        decision = Decision(
            rationale="test",
            verdict="violation" if flagged else "allow",
            rule_ids=("R5",) if flagged else (),
            confidence=0.9,
        )
        return Classification(
            decision=decision,
            model=self._name,
            prompt_version="t",
            policy_version="v0",
            latency_ms=1.0,
        )


def _scripted(*actions: Mapping[str, object]) -> SimulatedAgent:
    answers = iter(json.dumps(a) for a in actions)
    responder: Callable[[LLMRequest], str] = lambda _: next(answers)  # noqa: E731
    return SimulatedAgent(FakeBackend(responder), "You are Lark.")


async def _collect(
    conversation: ObservedConversation,
    history: Sequence[TraceEvent] = (),
    message: str = "Refund LK-50211 please",
    *,
    see_history: bool = True,
) -> list[TurnUpdate]:
    office = BackOffice.seeded()
    office.replay(history)
    return [
        u
        async for u in conversation.turn(
            office, ANA, history, message, observers_see_history=see_history
        )
    ]


def _events(updates: list[TurnUpdate]) -> list[TraceEvent]:
    return [u.event for u in updates if isinstance(u, EventOccurred)]


async def test_a_flagged_tool_call_still_runs_and_every_event_is_judged() -> None:
    agent = _scripted(
        {
            "action": "tool_call",
            "tool_name": "issue_refund",
            "arguments": {"order_id": "LK-50211", "amount": 129.99, "reason": "defect"},
        },
        {"action": "reply", "content": "Done, refunded."},
    )
    guard = RecordingClassifier()
    updates = await _collect(ObservedConversation(agent, [("llm", guard)]))

    kinds = [e.kind for e in _events(updates)]
    assert kinds == ["user_input", "tool_call", "tool_response", "model_output"]
    # The guardrail flagged the refund, and the refund happened anyway: it only observes.
    refund = _events(updates)[2]
    assert refund.content is not None
    assert json.loads(refund.content)["status"] == "issued"
    verdicts = {
        u.index: u.classification.decision.verdict for u in updates if isinstance(u, EventJudged)
    }
    assert verdicts == {0: "allow", 1: "violation", 2: "allow", 3: "allow"}
    assert isinstance(updates[-1], TurnFinished)
    # Each event is judged after it occurs, never before.
    occurred = [i for i, u in enumerate(updates) if isinstance(u, EventOccurred)]
    judged = {u.index: i for i, u in enumerate(updates) if isinstance(u, EventJudged)}
    for index, position in enumerate(occurred):
        assert judged[index] > position


async def test_observers_see_the_earlier_events_only_when_asked() -> None:
    history = [
        TraceEvent(kind="user_input", content="hi"),
        TraceEvent(kind="model_output", content="Hello"),
    ]
    reply = {"action": "reply", "content": "Sure."}

    with_history = RecordingClassifier()
    await _collect(ObservedConversation(_scripted(reply), [("llm", with_history)]), history)
    without = RecordingClassifier()
    await _collect(
        ObservedConversation(_scripted(reply), [("llm", without)]), history, see_history=False
    )

    assert [len(r.history) for r in with_history.requests] == [2, 3]
    assert all(r.history == () for r in without.requests)
    assert all(r.session == ANA for r in with_history.requests)


async def test_indices_continue_from_the_history() -> None:
    history = [
        TraceEvent(kind="user_input", content="hi"),
        TraceEvent(kind="model_output", content="Hello"),
    ]
    updates = await _collect(
        ObservedConversation(_scripted({"action": "reply", "content": "Sure."}), []), history
    )
    assert [u.index for u in updates if isinstance(u, EventOccurred)] == [2, 3]


async def test_every_observer_judges_every_event() -> None:
    agent = _scripted({"action": "reply", "content": "Hi."})
    updates = await _collect(
        ObservedConversation(
            agent, [("llm", RecordingClassifier()), ("baseline", RecordingClassifier())]
        )
    )
    engines = sorted((u.index, u.engine) for u in updates if isinstance(u, EventJudged))
    assert engines == [(0, "baseline"), (0, "llm"), (1, "baseline"), (1, "llm")]


async def test_a_crashing_observer_fails_closed() -> None:
    agent = _scripted({"action": "reply", "content": "Hi."})
    updates = await _collect(ObservedConversation(agent, [("llm", RecordingClassifier(fail=True))]))
    judged = [u for u in updates if isinstance(u, EventJudged)]
    assert len(judged) == 2
    assert all(u.classification.decision.verdict == "policy_silent" for u in judged)
    assert all(u.classification.error == "guard crashed" for u in judged)


async def test_an_agent_failure_stops_the_turn_but_keeps_the_verdicts() -> None:
    agent = SimulatedAgent(FakeBackend(lambda _: "not an action"), "You are Lark.")
    updates = await _collect(ObservedConversation(agent, [("llm", RecordingClassifier())]))
    assert [e.kind for e in _events(updates)] == ["user_input"]
    stopped = [u for u in updates if isinstance(u, TurnStopped)]
    assert "simulated agent failed" in stopped[0].reason
    assert any(isinstance(u, EventJudged) for u in updates)
    assert isinstance(updates[-1], TurnFinished)


async def test_a_turn_that_never_replies_is_cut_off() -> None:
    lookup = {
        "action": "tool_call",
        "tool_name": "lookup_order",
        "arguments": {"order_id": "LK-50211"},
    }
    agent = _scripted(*[lookup] * 3)
    updates = await _collect(ObservedConversation(agent, [], max_actions=3))
    assert len(_events(updates)) == 1 + 3 * 2
    assert any(isinstance(u, TurnStopped) and "3 actions" in u.reason for u in updates)


async def test_closing_the_stream_early_cancels_the_turn() -> None:
    agent = _scripted({"action": "reply", "content": "Hi."})
    conversation = ObservedConversation(agent, [("llm", RecordingClassifier())])
    stream = conversation.turn(BackOffice.seeded(), ANA, [], "hello")
    first = await anext(stream)
    assert isinstance(first, EventOccurred)
    await stream.aclose()  # must not hang or leak pending tasks
