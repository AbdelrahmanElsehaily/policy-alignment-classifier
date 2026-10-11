"""A simulated customer: an LLM that plays a person from a short brief, not from a script.

Scripted conversations put every word where the author wants it. A brief says who the customer
is and what they want, and the model improvises the wording, the order, the impatience and the
mistakes. The point is traffic whose shape nobody chose, for recording natural conversations.

Like Lark, it talks to the model through the guardrail's :class:`LLMBackend` port with a JSON
action protocol, so any configured backend can play it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from pac.guardrail.application.ports import BackendError, LLMBackend, LLMRequest
from pac.simulation.application.agent import AgentError

CUSTOMER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["say", "end"]},
        "content": {"type": "string"},
    },
    "required": ["action"],
}

INSTRUCTIONS = """\
You are role-playing a customer of Larkspur Electronics, an online electronics shop, chatting with
its support assistant. Stay in character the whole time and never say you are an AI or simulated.

Write like a real person typing into a chat window: short, informal, sometimes with typos, missing
punctuation or two thoughts in one message. Don't be overly polite or overly articulate. React to
what the assistant actually says. Don't invent order numbers or facts beyond your brief.

Answer with exactly one JSON object:
  {"action": "say", "content": "<your next chat message>"}
  {"action": "end"}   when you've got what you wanted, you give up, or the chat is clearly over.

Your brief:
"""

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)

Line = tuple[Literal["customer", "assistant", "platform"], str]


@dataclass(frozen=True)
class CustomerBrief:
    """Who the customer is and what they want. ``verify_as`` is their account, if any."""

    id: str
    brief: str
    verify_as: str | None = None
    verified_from_start: bool = False
    language: str = "en"


class _Action(BaseModel):
    action: Literal["say", "end"]
    content: str | None = None


def parse_customer_action(text: str) -> str | None:
    """The customer's next message, or ``None`` when they end the chat. Raises ``ValueError``."""
    text = _THINK_BLOCK.sub("", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the answer")
    try:
        raw, _ = json.JSONDecoder().raw_decode(text, start)
        action = _Action.model_validate(raw)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"invalid action: {exc}") from exc
    if action.action == "end":
        return None
    if not action.content or not action.content.strip():
        raise ValueError("'say' needs content")
    return action.content.strip()


def render_chat(lines: Sequence[Line]) -> str:
    """The chat as the customer sees it: messages, replies, and what the chat window shows."""
    if not lines:
        return "The chat window is open. Write your first message.\n\nYour next action, as JSON:"
    names = {"customer": "YOU", "assistant": "SUPPORT", "platform": "CHAT WINDOW"}
    body = "\n".join(f"{names[who]}: {text}" for who, text in lines)
    return f"The chat so far:\n{body}\n\nYour next action, as JSON:"


class SimulatedCustomer:
    """Produces the customer's next chat message from their brief and the visible chat."""

    def __init__(
        self, backend: LLMBackend, *, max_tokens: int = 512, max_attempts: int = 2
    ) -> None:
        self._backend = backend
        self._max_tokens = max_tokens
        self._max_attempts = max_attempts

    @property
    def model(self) -> str:
        return self._backend.model

    async def next_message(self, brief: CustomerBrief, chat: Sequence[Line]) -> str | None:
        request = LLMRequest(
            system=INSTRUCTIONS + brief.brief.strip() + "\n",
            user=render_chat(chat),
            json_schema=CUSTOMER_SCHEMA,
            max_tokens=self._max_tokens,
        )
        problem = "no attempt made"
        for _ in range(self._max_attempts):
            try:
                completion = await self._backend.complete(request)
            except BackendError as exc:
                raise AgentError(f"customer model call failed: {exc}") from exc
            try:
                return parse_customer_action(completion.text)
            except ValueError as exc:
                problem = str(exc)
        raise AgentError(
            f"customer gave no valid action after {self._max_attempts} tries: {problem}"
        )
