"""What is measured about each judged event: features of the input, and of the classifier's answer.

Every feature is categorical (numbers are bucketed), so one statistic (PSI) covers them all and
a report can say which category moved. Input features describe the traffic, so their drift means
the world changed. Output features describe the classifier, so their drift with stable inputs
means the classifier changed (a new model, prompt or provider behaviour).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Literal

from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.enforcement import gate_for
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.value_object import ValueObject

FeatureGroup = Literal["input", "output"]

INPUT_FEATURES = ("kind", "tool", "verified", "text_length", "script", "history")
OUTPUT_FEATURES = ("verdict", "gate", "rules", "confidence", "error", "latency")
# Properties of whichever model answers, with no expected value: a reference built from labels
# leaves them out, and they are reported as not compared.
MODEL_FEATURES = ("confidence", "error", "latency")
FEATURE_GROUP: dict[str, FeatureGroup] = {
    **dict.fromkeys(INPUT_FEATURES, "input"),
    **dict.fromkeys(OUTPUT_FEATURES, "output"),
}


class Observation(ValueObject):
    """One judged event as monitoring sees it: one or more categories per feature."""

    values: dict[str, tuple[str, ...]]


def bucket(value: float, edges: Sequence[float], labels: Sequence[str]) -> str:
    """The label of the first bucket whose upper edge exceeds ``value`` (the last otherwise)."""
    for edge, label in zip(edges, labels, strict=False):
        if value < edge:
            return label
    return labels[-1]


_SCRIPTS = (
    ("arabic", 0x0600, 0x06FF),
    ("cyrillic", 0x0400, 0x04FF),
    ("cjk", 0x4E00, 0x9FFF),
    ("devanagari", 0x0900, 0x097F),
)


def script_of(text: str) -> str:
    """The writing system most letters are in: latin, arabic, …, ``mixed`` or ``none``."""
    counts: dict[str, int] = {}
    for ch in text:
        if not ch.isalpha():
            continue
        code = ord(ch)
        name = next((n for n, lo, hi in _SCRIPTS if lo <= code <= hi), None)
        if name is None:
            name = "latin" if code < 0x0250 else "other"
        counts[name] = counts.get(name, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return "none"
    name, top = max(counts.items(), key=lambda kv: kv[1])
    return name if top / total >= 0.6 else "mixed"


def _inputs(request: ClassificationRequest) -> dict[str, tuple[str, ...]]:
    event = request.event
    text = event.content or ""
    if event.arguments is not None:
        text = json.dumps(event.arguments, ensure_ascii=False)
    return {
        "kind": (event.kind,),
        "tool": (event.tool_name or "n/a",),
        "verified": ("verified" if request.session.verified else "unverified",),
        "text_length": (
            bucket(
                len(text), (50, 150, 400, 1000), ("<50", "50-150", "150-400", "400-1000", "1000+")
            ),
        ),
        "script": (script_of(text),),
        "history": (
            bucket(len(request.history), (1, 3, 6, 11), ("0", "1-2", "3-5", "6-10", "11+")),
        ),
    }


def _answer(verdict: Verdict, rule_ids: Sequence[str]) -> dict[str, tuple[str, ...]]:
    # Rules count only on violations, where they say which rule is broken. Labels cite none on
    # other verdicts, and a model citing the rule it couldn't decide shouldn't read as drift.
    broken = tuple(rule_ids) if verdict == "violation" else ()
    return {
        "verdict": (verdict,),
        "gate": (gate_for(verdict),),
        "rules": broken or ("none",),
    }


def observe(request: ClassificationRequest, result: Classification) -> Observation:
    """The features of one judged event."""
    decision = result.decision
    values: dict[str, tuple[str, ...]] = {
        **_inputs(request),
        **_answer(decision.verdict, decision.rule_ids),
        "confidence": (
            bucket(decision.confidence, (0.5, 0.8, 0.95), ("<0.5", "0.5-0.8", "0.8-0.95", "0.95+")),
        ),
        "error": ("error" if result.error else "ok",),
        "latency": (
            bucket(
                result.model_latency_ms,
                (500, 2000, 5000, 15000),
                ("<0.5s", "0.5-2s", "2-5s", "5-15s", "15s+"),
            ),
        ),
    }
    return Observation(values=values)


def observe_expected(
    request: ClassificationRequest, verdict: Verdict, rule_ids: Sequence[str]
) -> Observation:
    """An event with its expected (labelled) answer: what the classifier should say about it.

    A reference built this way doesn't depend on any model; the model's own properties
    (``MODEL_FEATURES``) are left out.
    """
    return Observation(values={**_inputs(request), **_answer(verdict, rule_ids)})
