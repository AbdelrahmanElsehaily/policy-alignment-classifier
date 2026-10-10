"""One conversation turn with the guardrail watching: the agent acts, the guardrail judges.

The guardrail is a man in the middle. It sees each event the moment it happens and judges it
concurrently, but it cannot stop the agent: a flagged tool call still runs, and the agent
carries on. What a deployment does with a verdict (alert, hold a reply, page a human) is the
operator's decision, so the turn reports verdicts and changes nothing because of them.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from typing import Literal

from pac.guardrail.domain.classification import Classification, EventClassifier
from pac.guardrail.domain.decision import Decision
from pac.guardrail.domain.events import ClassificationRequest, Session, TraceEvent
from pac.guardrail.domain.value_object import ValueObject
from pac.simulation.application.agent import AgentError, SimulatedAgent
from pac.simulation.domain.back_office import BackOffice


class EventOccurred(ValueObject):
    type: Literal["event"] = "event"
    index: int
    event: TraceEvent


class EventJudged(ValueObject):
    type: Literal["verdict"] = "verdict"
    index: int
    engine: str
    classification: Classification


class TurnStopped(ValueObject):
    type: Literal["stopped"] = "stopped"
    reason: str


class TurnFinished(ValueObject):
    type: Literal["done"] = "done"


TurnUpdate = EventOccurred | EventJudged | TurnStopped | TurnFinished


class ObservedConversation:
    """Runs turns of the simulated agent and streams each event with every observer's verdict.

    ``observers`` maps an engine name (``"llm"``, ``"baseline"``) to a classifier. Each observer
    judges every event, with the earlier events as history when ``observers_see_history``.
    """

    def __init__(
        self,
        agent: SimulatedAgent,
        observers: Sequence[tuple[str, EventClassifier]],
        *,
        max_actions: int = 6,
        history_window: int = 20,
    ) -> None:
        self._agent = agent
        self._observers = list(observers)
        self._max_actions = max_actions
        self._history_window = history_window

    async def turn(
        self,
        office: BackOffice,
        session: Session,
        history: Sequence[TraceEvent],
        message: str,
        *,
        observers_see_history: bool = True,
    ) -> AsyncGenerator[TurnUpdate]:
        """The customer says ``message``; yield events and verdicts as they happen.

        ``office`` must already reflect ``history`` (see :meth:`BackOffice.replay`).
        """
        queue: asyncio.Queue[TurnUpdate | None] = asyncio.Queue()
        transcript = list(history)
        judges: set[asyncio.Task[None]] = set()

        def occur(event: TraceEvent) -> None:
            index = len(transcript)
            transcript.append(event)
            queue.put_nowait(EventOccurred(index=index, event=event))
            earlier = transcript[max(0, index - self._history_window) : index]
            request = ClassificationRequest(
                event=event,
                session=session,
                history=tuple(earlier) if observers_see_history else (),
            )
            for engine, classifier in self._observers:
                task = asyncio.create_task(_judge(queue, engine, classifier, index, request))
                judges.add(task)
                task.add_done_callback(judges.discard)

        async def act() -> None:
            occur(TraceEvent(kind="user_input", content=message))
            for _ in range(self._max_actions):
                event = await self._agent.next_event(session, transcript)
                occur(event)
                if event.kind == "model_output":
                    return
                occur(office.execute(event))
            queue.put_nowait(
                TurnStopped(reason=f"Lark took {self._max_actions} actions without replying.")
            )

        async def drive() -> None:
            try:
                await act()
            except AgentError as exc:
                queue.put_nowait(TurnStopped(reason=f"The simulated agent failed: {exc}"))
            except Exception as exc:  # never leave the stream hanging on a bug
                queue.put_nowait(TurnStopped(reason=f"The turn failed: {exc!r}"))
            finally:
                while judges:
                    await asyncio.gather(*judges)
                queue.put_nowait(None)

        driver = asyncio.create_task(drive())
        try:
            while (update := await queue.get()) is not None:
                yield update
            yield TurnFinished()
        finally:
            # The client went away mid-turn: stop the agent and any pending verdicts.
            for task in (driver, *judges):
                task.cancel()


async def _judge(
    queue: asyncio.Queue[TurnUpdate | None],
    engine: str,
    classifier: EventClassifier,
    index: int,
    request: ClassificationRequest,
) -> None:
    try:
        classification = await classifier.classify(request)
    except Exception as exc:  # a guardrail that raises must still fail closed, never vanish
        classification = Classification(
            decision=Decision.fail_closed(f"{type(exc).__name__}: {exc}"),
            model=classifier.name,
            prompt_version="n/a",
            policy_version="n/a",
            latency_ms=0.0,
            error=str(exc) or type(exc).__name__,
        )
    queue.put_nowait(EventJudged(index=index, engine=engine, classification=classification))
