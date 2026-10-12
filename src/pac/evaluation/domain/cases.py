"""Labelled eval cases: an event to judge, its gold verdict, and the reasoning behind it."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal, Self

from pydantic import Field, model_validator

from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.policy import Policy
from pac.guardrail.domain.value_object import ValueObject

LabelConfidence = Literal["high", "medium", "contested"]
# dev and test live in data/eval/dev_test.jsonl (frozen with their committed runs). hard
# (hand-written) and stress (generated) came later and each has its own file, so adding them
# changed nothing the earlier runs depend on. agents: conversations recorded with another agent
# model (MiniMax-M3), in their own file for the same reason. agents-pressure: the same briefs with
# a tool-encouraging line in the agent's instructions. train (generated) is for prompt
# optimisation only, never for reporting.
# "hard" is historical: those cases now open the stress set, but runs recorded before keep it.
Split = Literal[
    "dev", "test", "hard", "stress", "agents", "agents-pressure", "long-context", "train"
]


class DatasetError(ValueError):
    """The eval set is inconsistent with itself or with the policy it is measured against."""


class LabeledCase(ValueObject):
    """One eval case: a request, its gold label, and the reasoning behind that label."""

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    split: Split
    request: ClassificationRequest
    label: Verdict
    rule_ids: tuple[str, ...] = ()
    label_confidence: LabelConfidence
    rationale: str = Field(min_length=1)
    tags: tuple[str, ...] = ()
    origin: Literal["targeted", "transcript"] = "targeted"
    # The answer once the classifier also sees ``history`` (history ablation only). Set where
    # earlier events supply a fact the event lacks; otherwise ``label`` applies in both settings.
    label_with_history: Verdict | None = None
    rule_ids_with_history: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _label_has_rules(self) -> Self:
        if self.label == "violation" and not self.rule_ids:
            raise ValueError(f"{self.id}: violation label must cite a rule")
        if self.label_with_history == "violation" and not self.rule_ids_with_history:
            raise ValueError(f"{self.id}: violation label_with_history must cite a rule")
        if self.label_with_history is not None and not self.request.history:
            raise ValueError(f"{self.id}: label_with_history needs history")
        return self

    def gold(self, *, with_history: bool) -> tuple[Verdict, tuple[str, ...]]:
        """The label to score against in the given setting."""
        if with_history and self.label_with_history is not None:
            return self.label_with_history, self.rule_ids_with_history
        return self.label, self.rule_ids

    def request_for(self, *, with_history: bool) -> ClassificationRequest:
        """What the classifier is shown: one event, plus history only in the ablation."""
        return self.request if with_history else self.request.without_history()


def ensure_unique_ids(cases: Sequence[LabeledCase]) -> None:
    duplicates = sorted(i for i, n in Counter(c.id for c in cases).items() if n > 1)
    if duplicates:
        raise DatasetError(f"duplicate case ids: {duplicates}")


def check_against_policy(cases: Iterable[LabeledCase], policy: Policy) -> None:
    """Every rule a label cites must exist in the policy it is measured against."""
    for case in cases:
        unknown = sorted(
            r for r in (*case.rule_ids, *case.rule_ids_with_history) if not policy.defines(r)
        )
        if unknown:
            raise DatasetError(f"{case.id}: cites rules not in policy: {unknown}")


def select(cases: Iterable[LabeledCase], split: Split | None) -> list[LabeledCase]:
    return [c for c in cases if split is None or c.split == split]
