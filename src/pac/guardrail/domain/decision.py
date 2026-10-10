"""The guardrail's judgement on one event."""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, model_validator

from pac.guardrail.domain.value_object import ValueObject

Verdict = Literal["allow", "violation", "policy_silent"]
VERDICTS: tuple[Verdict, ...] = ("allow", "violation", "policy_silent")

RULE_ID_PATTERN = re.compile(r"^R\d{1,2}$")
MAX_RATIONALE_CHARS = 2000


class Decision(ValueObject):
    """The judgement itself: exactly what the LLM is asked to produce.

    Field order matters: the rationale comes first so the model reasons before committing.
    """

    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARS)
    verdict: Verdict
    rule_ids: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _rules_are_well_formed(self) -> Self:
        bad = [r for r in self.rule_ids if not RULE_ID_PATTERN.match(r)]
        if bad:
            raise ValueError(f"malformed rule ids: {bad}")
        if self.verdict == "violation" and not self.rule_ids:
            raise ValueError("a violation must cite at least one rule")
        return self

    @classmethod
    def fail_closed(cls, reason: str) -> Decision:
        """The decision when the classifier itself fails.

        A guardrail that fails open is not a guardrail, but failing to ``violation`` would block
        legitimate traffic on every outage. ``policy_silent`` routes the event to a human.
        """
        return cls(
            rationale=f"Classifier error, routed to human review: {reason}"[:MAX_RATIONALE_CHARS],
            verdict="policy_silent",
            rule_ids=(),
            confidence=0.0,
        )
