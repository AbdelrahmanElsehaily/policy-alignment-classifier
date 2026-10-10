"""One classifier output, joined with the gold label it is scored against."""

from __future__ import annotations

from typing import Literal

from pac.evaluation.domain.cases import LabelConfidence, LabeledCase, Split
from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.events import EventKind
from pac.guardrail.domain.value_object import ValueObject


class Prediction(ValueObject):
    """One classifier output joined with the gold label it is scored against."""

    case_id: str
    repeat: int = 0
    split: Split
    kind: EventKind
    gold: Verdict
    gold_rules: tuple[str, ...]
    label_confidence: LabelConfidence
    tags: tuple[str, ...]
    origin: str
    result: Classification

    @property
    def predicted(self) -> Verdict:
        """The verdict acted on in production (an error fails closed to ``policy_silent``)."""
        return self.result.decision.verdict

    @property
    def outcome(self) -> Verdict | Literal["error"]:
        """What the classifier itself decided; ``"error"`` when it failed and nothing was decided.

        Used for scoring, so a fail-closed error is never credited as a correct ``policy_silent``.
        """
        return "error" if self.result.error is not None else self.result.decision.verdict

    @property
    def correct(self) -> bool:
        return self.outcome == self.gold

    @classmethod
    def from_case(
        cls,
        case: LabeledCase,
        result: Classification,
        repeat: int = 0,
        *,
        with_history: bool = False,
    ) -> Prediction:
        gold, gold_rules = case.gold(with_history=with_history)
        return cls(
            case_id=case.id,
            repeat=repeat,
            split=case.split,
            kind=case.request.event.kind,
            gold=gold,
            gold_rules=gold_rules,
            label_confidence=case.label_confidence,
            tags=case.tags,
            origin=case.origin,
            result=result,
        )
