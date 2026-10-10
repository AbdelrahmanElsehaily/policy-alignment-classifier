"""What the guardrail does with a verdict. It sits inline, so it can stop an event.

The guardrail is a man in the middle on the traffic between the customer, the agent and the
agent's tools. It cannot change the agent, but every event passes through it and waits for its
verdict:

- ``allow`` passes the event on.
- ``violation`` blocks it.
- ``policy_silent`` holds it for a human. That includes the classifier's own failures, which
  fail closed to ``policy_silent``.

A stopped event is replaced, so both sides of the conversation can carry on. The agent gets an
error in place of a blocked tool call or tool result; the customer gets a notice in place of a
blocked message or reply. A stand-in is the guardrail's own event and is never judged.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.events import TraceEvent

Gate = Literal["pass", "block", "hold"]

_GATES: dict[Verdict, Gate] = {"allow": "pass", "violation": "block", "policy_silent": "hold"}


def gate_for(verdict: Verdict) -> Gate:
    return _GATES[verdict]


def stand_in(event: TraceEvent, gate: Gate, rule_ids: Sequence[str] = ()) -> TraceEvent:
    """What the guardrail sends on in place of an event it stopped.

    Tool calls and tool results are replaced towards the agent (an error it can react to, for
    example by escalating). Customer messages and agent replies are replaced towards the
    customer. The wording is a placeholder for copy the operator would write.
    """
    if gate == "pass":
        raise ValueError("only a stopped event has a stand-in")
    rules = f" ({', '.join(rule_ids)})" if rule_ids else ""
    reason = (
        f"blocked by the policy guardrail{rules}"
        if gate == "block"
        else "held for human review by the policy guardrail"
    )
    match event.kind:
        case "tool_call" | "tool_response":
            return TraceEvent(
                kind="tool_response",
                tool_name=event.tool_name,
                content=json.dumps({"error": f"This tool {_what(event)} was {reason}."}),
            )
        case "user_input":
            return TraceEvent(kind="model_output", content=f"[Your message was {reason}.]")
        case "model_output":
            return TraceEvent(kind="model_output", content=f"[This reply was {reason}.]")


def _what(event: TraceEvent) -> str:
    return "call" if event.kind == "tool_call" else "result"
