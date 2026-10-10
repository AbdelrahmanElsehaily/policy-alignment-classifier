"""One conversation turn with the guardrail inline: every event waits for its verdict.

The guardrail is a man in the middle. It cannot change the agent, but every event passes
through it: the customer's message on its way to the agent, each tool call on its way to the
back office, each tool result on its way back, and each reply on its way to the customer.
Nothing moves on until the gatekeeper has judged it. ``allow`` passes, ``violation`` blocks,
``policy_silent`` holds for a human, and a stopped event is replaced by the guardrail's stand-in
(see :mod:`pac.guardrail.domain.enforcement`).

Other observers (the regex baseline) judge the same events in the background, for comparison;
they never gate.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterable, Sequence
from typing import Literal

from pac.guardrail.domain.classification import Classification, EventClassifier
from pac.guardrail.domain.decision import Decision
from pac.guardrail.domain.enforcement import Gate, gate_for, stand_in
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.guardrail.domain.value_object import ValueObject
from pac.simulation.application.agent import AgentError, SimulatedAgent
from pac.simulation.domain.back_office import BackOffice

GATEKEEPER = "llm"


class Entry(ValueObject):
    """One event in the conversation and what the guardrail did with it.

    ``by_guardrail`` marks the guardrail's own stand-ins, which are never judged or gated.
    """

    event: TraceEvent
    gate: Gate = "pass"
    by_guardrail: bool = False


def agent_view(entries: Iterable[Entry]) -> list[TraceEvent]:
    """What the agent knows: everything it did, and only what the guardrail let through to it.

    It remembers its own tool calls and replies even when they were stopped, and it sees the
    stand-ins that replace stopped tool calls and results. It never sees a stopped customer
    message or tool result, nor the notices the guardrail sends the customer.
    """
    view = []
    for entry in entries:
        kind = entry.event.kind
        if entry.by_guardrail:
            visible = kind == "tool_response"
        else:
            visible = kind in ("tool_call", "model_output") or entry.gate == "pass"
        if visible:
            view.append(entry.event)
    return view


def guardrail_view(entries: Iterable[Entry]) -> list[TraceEvent]:
    """What the guardrail has seen: every original event, stopped or not, but not its own."""
    return [e.event for e in entries if not e.by_guardrail]


def executed_calls(entries: Iterable[Entry]) -> list[TraceEvent]:
    """The tool calls that actually reached the back office."""
    return [
        e.event
        for e in entries
        if not e.by_guardrail and e.event.kind == "tool_call" and e.gate == "pass"
    ]


class EventOccurred(ValueObject):
    type: Literal["event"] = "event"
    index: int
    event: TraceEvent
    by_guardrail: bool = False


class EventJudged(ValueObject):
    type: Literal["verdict"] = "verdict"
    index: int
    engine: str
    classification: Classification


class EventGated(ValueObject):
    type: Literal["gate"] = "gate"
    index: int
    gate: Gate


class TurnStopped(ValueObject):
    type: Literal["stopped"] = "stopped"
    reason: str


class TurnFinished(ValueObject):
    type: Literal["done"] = "done"


TurnUpdate = EventOccurred | EventJudged | EventGated | TurnStopped | TurnFinished


class GuardedConversation:
    """Runs turns of the simulated agent with the gatekeeper inline on every event."""

    def __init__(
        self,
        agent: SimulatedAgent,
        gatekeeper: EventClassifier,
        observers: Sequence[tuple[str, EventClassifier]] = (),
        *,
        max_actions: int = 6,
        history_window: int = 20,
    ) -> None:
        self._agent = agent
        self._gatekeeper = gatekeeper
        self._observers = list(observers)
        self._max_actions = max_actions
        self._history_window = history_window

    async def turn(
        self,
        office: BackOffice,
        session: Session,
        history: Sequence[Entry],
        message: str,
        *,
        guardrail_sees_history: bool = True,
    ) -> AsyncGenerator[TurnUpdate]:
        """The customer says ``message``; yield events, verdicts and gates as they happen.

        ``office`` must already reflect ``history`` (replay :func:`executed_calls`).
        """
        turn = _Turn(self, office, session, history, guardrail_sees_history)
        driver = asyncio.create_task(turn.drive(message))
        try:
            while (update := await turn.queue.get()) is not None:
                yield update
            yield TurnFinished()
        finally:
            # The client went away mid-turn: stop the agent and any pending verdicts.
            for task in (driver, *turn.observers):
                task.cancel()


class _Turn:
    """The state of one turn in progress: the entries so far and the updates to stream."""

    def __init__(
        self,
        conversation: GuardedConversation,
        office: BackOffice,
        session: Session,
        history: Sequence[Entry],
        guardrail_sees_history: bool,
    ) -> None:
        self._c = conversation
        self._office = office
        self._session = session
        self._sees_history = guardrail_sees_history
        self._entries = list(history)
        self.queue: asyncio.Queue[TurnUpdate | None] = asyncio.Queue()
        self.observers: set[asyncio.Task[None]] = set()

    async def drive(self, message: str) -> None:
        try:
            await self._act(message)
        except AgentError as exc:
            self.queue.put_nowait(TurnStopped(reason=f"The simulated agent failed: {exc}"))
        except Exception as exc:  # never leave the stream hanging on a bug
            self.queue.put_nowait(TurnStopped(reason=f"The turn failed: {exc!r}"))
        finally:
            while self.observers:
                await asyncio.gather(*self.observers)
            self.queue.put_nowait(None)

    async def _act(self, message: str) -> None:
        if not await self._guard(self._add(TraceEvent(kind="user_input", content=message))):
            return  # the agent never receives a stopped message
        for _ in range(self._c._max_actions):
            event = await self._c._agent.next_event(self._session, agent_view(self._entries))
            passed = await self._guard(self._add(event))
            if event.kind == "model_output":
                return
            if passed:
                await self._guard(self._add(self._office.execute(event)))
        self.queue.put_nowait(
            TurnStopped(reason=f"Lark took {self._c._max_actions} actions without replying.")
        )

    def _add(self, event: TraceEvent, *, by_guardrail: bool = False) -> int:
        self._entries.append(Entry(event=event, by_guardrail=by_guardrail))
        index = len(self._entries) - 1
        self.queue.put_nowait(EventOccurred(index=index, event=event, by_guardrail=by_guardrail))
        return index

    async def _guard(self, index: int) -> bool:
        """Judge one event, gate it, and put a stand-in in its place if stopped."""
        event = self._entries[index].event
        seen = guardrail_view(self._entries[:index])[-self._c._history_window :]
        request = ClassificationRequest(
            event=event,
            session=self._session,
            history=tuple(seen) if self._sees_history else (),
        )
        for engine, classifier in self._c._observers:
            task = asyncio.create_task(_observe(self.queue, engine, classifier, index, request))
            self.observers.add(task)
            task.add_done_callback(self.observers.discard)

        classification = await _classify(self._c._gatekeeper, request)
        self.queue.put_nowait(
            EventJudged(index=index, engine=GATEKEEPER, classification=classification)
        )
        gate = gate_for(classification.decision.verdict)
        self._entries[index] = self._entries[index].model_copy(update={"gate": gate})
        self.queue.put_nowait(EventGated(index=index, gate=gate))
        if gate != "pass":
            self._add(stand_in(event, gate, classification.decision.rule_ids), by_guardrail=True)
        return gate == "pass"


async def _classify(classifier: EventClassifier, request: ClassificationRequest) -> Classification:
    try:
        return await classifier.classify(request)
    except Exception as exc:  # a guardrail that raises must still fail closed, never vanish
        return Classification(
            decision=Decision.fail_closed(f"{type(exc).__name__}: {exc}"),
            model=classifier.name,
            prompt_version="n/a",
            policy_version="n/a",
            latency_ms=0.0,
            error=str(exc) or type(exc).__name__,
        )


async def _observe(
    queue: asyncio.Queue[TurnUpdate | None],
    engine: str,
    classifier: EventClassifier,
    index: int,
    request: ClassificationRequest,
) -> None:
    classification = await _classify(classifier, request)
    queue.put_nowait(EventJudged(index=index, engine=engine, classification=classification))
