"""Record a natural conversation: a simulated customer and Lark, with no guardrail in the way.

The recording is raw material for the eval, so nothing judges or stops the events: the trace
shows what the agent does on its own. Verification happens as the chat platform would do it.
When Lark asks the customer to verify, the customer enters the code, and from the next event on
the session envelope says verified. The customer only sees what a chat window shows: their own
messages, Lark's replies, and the platform's notices, never tool calls or results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pac.guardrail.domain.events import Session, TraceEvent
from pac.simulation.application.agent import AgentError, SimulatedAgent
from pac.simulation.application.customer import CustomerBrief, Line, SimulatedCustomer
from pac.simulation.domain.back_office import BackOffice

# Lark answers in the customer's language, so the platform must notice a request to verify in any
# of the briefs' languages: "verif" covers Spanish, Portuguese and German; French and Arabic need
# their own. (Before these were added, the Arabic briefs were never verified.)
_VERIFY_HINTS = (
    "verif",
    "vérif",
    "تحقق",
    "تحقّق",
    "one-time code",
    "code we sent",
    "code to your email",
    "security code",
)


@dataclass
class Recording:
    brief: CustomerBrief
    events: list[tuple[Session, TraceEvent]] = field(default_factory=list)
    stopped: str | None = None


def asks_to_verify(reply: str) -> bool:
    text = reply.lower()
    return any(hint in text for hint in _VERIFY_HINTS)


async def record_conversation(
    brief: CustomerBrief,
    customer: SimulatedCustomer,
    agent: SimulatedAgent,
    office: BackOffice,
    customers: dict[str, str],
    *,
    max_customer_turns: int = 8,
    max_actions: int = 6,
) -> Recording:
    """Run one conversation to its end. ``customers`` maps customer ids to emails on file."""
    recording = Recording(brief=brief)
    session = _verified(brief, customers) if brief.verified_from_start else Session()
    chat: list[Line] = []
    transcript: list[TraceEvent] = []

    def log(event: TraceEvent) -> None:
        recording.events.append((session, event))
        transcript.append(event)

    for _ in range(max_customer_turns):
        message = await customer.next_message(brief, chat)
        if message is None:
            return recording
        chat.append(("customer", message))
        log(TraceEvent(kind="user_input", content=message))
        for _ in range(max_actions):
            try:
                event = await agent.next_event(session, transcript)
            except AgentError as exc:
                # A real agent failure (e.g. an action outside its protocol): keep what was
                # recorded and say why it ends, rather than losing the conversation.
                recording.stopped = f"Lark failed: {exc}"
                return recording
            log(event)
            if event.kind == "model_output":
                chat.append(("assistant", event.content or ""))
                break
            log(office.execute(event))
        else:
            recording.stopped = f"Lark took {max_actions} actions without replying"
            return recording
        if not session.verified and brief.verify_as and asks_to_verify(chat[-1][1]):
            session = _verified(brief, customers)
            chat.append(("platform", "You entered the code from your email. You are now verified."))
    recording.stopped = f"customer still talking after {max_customer_turns} turns"
    return recording


def _verified(brief: CustomerBrief, customers: dict[str, str]) -> Session:
    if brief.verify_as is None:
        return Session()
    return Session(
        verified=True,
        verified_customer_id=brief.verify_as,
        email_on_file=customers[brief.verify_as],
    )
