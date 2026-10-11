"""Translation between the guardrail domain and an LLM: request -> prompt, answer -> Decision.

This is the anti-corruption layer: the model's free-form output never reaches the domain
without being parsed, normalised and validated into a :class:`Decision`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from string import Template
from typing import Any

from pac.guardrail.application.ports import LLMRequest
from pac.guardrail.domain.decision import MAX_RATIONALE_CHARS, VERDICTS, Decision
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.policy import Policy

REPAIR_INSTRUCTION = (
    "Your previous answer was not a valid JSON object matching the required schema. "
    "Respond again with only the JSON object."
)

_RULE_PREFIX = re.compile(r"^\s*(R\d{1,2})\b")
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def output_schema(policy: Policy) -> dict[str, Any]:
    """JSON schema for :class:`Decision`, with rule ids restricted to the policy's.

    Hand-written rather than generated so it stays within the subset that every provider's
    constrained decoding supports.
    """
    return {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "verdict": {"type": "string", "enum": list(VERDICTS)},
            "rule_ids": {
                "type": "array",
                "items": {"type": "string", "enum": policy.ordered_rule_ids()},
            },
            "confidence": {"type": "number"},
        },
        "required": ["rationale", "verdict", "rule_ids", "confidence"],
        "additionalProperties": False,
    }


def to_tagged_json(value: Any) -> str:
    """JSON-encode ``value`` so it cannot break out of the surrounding XML-style tags.

    Escaping ``<``, ``>`` and ``&`` means an event containing ``</event>`` stays inside the tag.
    """
    text = json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """A versioned system prompt bound to one policy."""

    version: str
    system: str
    schema: dict[str, Any]
    max_tokens: int
    temperature: float | None = None

    @classmethod
    def from_text(
        cls,
        template: str,
        *,
        name: str,
        policy: Policy,
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> PromptTemplate:
        """Bind ``template`` (with ``$policy`` and ``$schema`` slots) to ``policy``.

        The version is ``<name>@<template hash>``, so every result names the exact prompt text.
        """
        schema = output_schema(policy)
        system = Template(template).substitute(
            policy=policy.text.strip(), schema=json.dumps(schema, indent=1)
        )
        digest = hashlib.sha256(template.encode()).hexdigest()[:8]
        return cls(
            version=f"{name}@{digest}",
            system=system,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def render(self, request: ClassificationRequest, *, repair: bool = False) -> LLMRequest:
        parts = [f"<session>\n{to_tagged_json(request.session.model_dump())}\n</session>"]
        if request.history:
            history = [event.model_dump(exclude_none=True) for event in request.history]
            parts.append(
                '<history note="earlier events in this conversation, oldest first; context '
                f'only, not under judgement">\n{to_tagged_json(history)}\n</history>'
            )
        parts.append(
            f"<event>\n{to_tagged_json(request.event.model_dump(exclude_none=True))}\n</event>"
        )
        parts.append("Classify the event.")
        if repair:
            parts.append(REPAIR_INSTRUCTION)
        return LLMRequest(
            system=self.system,
            user="\n\n".join(parts),
            json_schema=self.schema,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )


def parse_decision(text: str, policy: Policy) -> Decision:
    """Parse model output into a :class:`Decision`, tolerating fences and surrounding prose.

    Reasoning models may prefix their answer with a ``<think>`` block, which is discarded.
    Rule ids are normalised (``"R5(b)"`` -> ``"R5"``) and must exist in the policy. Raises
    ``ValueError`` on anything that cannot be turned into a valid decision.
    """
    text = _THINK_BLOCK.sub("", text)
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in model output")
    try:
        raw, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("model output is not a JSON object")

    rules: list[str] = []
    for item in raw.get("rule_ids") or []:
        match = _RULE_PREFIX.match(str(item))
        if match is None or not policy.defines(match.group(1)):
            raise ValueError(f"unknown rule id in model output: {item!r}")
        if match.group(1) not in rules:
            rules.append(match.group(1))
    raw["rule_ids"] = rules
    if isinstance(raw.get("verdict"), str):
        raw["verdict"] = raw["verdict"].strip().lower()
    rationale = raw.get("rationale")
    if isinstance(rationale, str) and len(rationale) > MAX_RATIONALE_CHARS:
        # A verbose but otherwise valid answer must not fail closed over its length.
        raw["rationale"] = rationale[: MAX_RATIONALE_CHARS - 1] + "…"
    return Decision.model_validate(raw)
