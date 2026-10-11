"""DSPy-free helpers for scripts/optimize_prompt.py: the prompt's shape, the metric, the splits.

Kept apart from the optimiser so the tests can pin them without DSPy installed
(tests/unit/test_prompt_optimization.py). Nothing in ``src/pac`` imports this.

The one design constraint everything here serves: **what DSPy optimises must be exactly what
the harness runs.** A candidate is the v2 prompt with its middle section (the judging
instructions) replaced and up to four worked examples added. ``assemble`` turns a candidate
into a prompt file with the same ``$policy`` and ``$schema`` slots as v2, the optimiser's
adapter renders it with :class:`PromptTemplate` and :func:`user_message`, and both are
byte-identical to what ``pac eval`` sends for the exported file.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pac.evaluation.domain.cases import LabelConfidence, LabeledCase
from pac.guardrail.application.prompting import to_tagged_json
from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.events import ClassificationRequest

MAX_DEMOS = 4
INSTRUCTIONS_START = "## How to judge"
OUTPUT_START = "## Output"
HISTORY_NOTE = (
    "earlier events in this conversation, oldest first; context only, not under judgement"
)
EXAMPLES_HEADING = "## Worked examples"
EXAMPLES_INTRO = (
    "Each example shows an input as you will receive it and a correct answer. They illustrate "
    "how to apply the rules above; judge every event on its own content."
)

# =============================================================================================
# The prompt: a fixed frame around optimisable instructions and demos
# =============================================================================================


@dataclass(frozen=True, slots=True)
class Frame:
    """A prompt template cut into its fixed head and tail and its optimisable middle.

    The head holds the role sentence and the ``$policy`` slot; the tail holds the output
    contract and the ``$schema`` slot. Keeping both fixed means no candidate can drop the
    policy or change the output schema, which the brief does not allow.
    """

    head: str
    instructions: str
    tail: str

    @classmethod
    def split(cls, template: str) -> Frame:
        start = template.index(INSTRUCTIONS_START)
        end = template.index(OUTPUT_START)
        return cls(
            head=template[:start], instructions=template[start:end].strip(), tail=template[end:]
        )


def escape_template(text: str) -> str:
    """Escape ``$`` so optimised text can't create or break a ``string.Template`` slot."""
    return text.replace("$", "$$")


@dataclass(frozen=True, slots=True)
class Demo:
    """One worked example: the inputs as the classifier renders them, and the answer."""

    session: str
    history: str
    event: str
    rationale: str
    verdict: str
    rule_ids: tuple[str, ...]
    confidence: float

    def answer(self) -> str:
        return json.dumps(
            {
                "rationale": self.rationale,
                "verdict": self.verdict,
                "rule_ids": list(self.rule_ids),
                "confidence": self.confidence,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_fields(cls, fields: Mapping[str, Any]) -> Demo:
        """From a DSPy demo (a bootstrapped prediction or a labelled train example)."""
        return cls(
            session=str(fields["session"]),
            history=str(fields.get("history") or ""),
            event=str(fields["event"]),
            rationale=str(fields["rationale"]),
            verdict=str(fields["verdict"]),
            rule_ids=tuple(str(r) for r in fields.get("rule_ids") or ()),
            confidence=round(float(fields.get("confidence") or 0.0), 2),
        )


def render_demos(demos: Sequence[Demo]) -> str:
    if len(demos) > MAX_DEMOS:
        raise ValueError(f"at most {MAX_DEMOS} demos are allowed, got {len(demos)}")
    parts = [EXAMPLES_HEADING, EXAMPLES_INTRO]
    for n, demo in enumerate(demos, start=1):
        parts.append(f"### Example {n}")
        parts.append(user_message(demo.session, demo.history, demo.event))
        parts.append(f"Answer:\n{demo.answer()}")
    return "\n\n".join(parts)


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
# Sections a candidate may not carry: the fixed tail owns the output format, and worked
# examples come only from the train split, at most MAX_DEMOS of them.
_FORBIDDEN_SECTION = re.compile(
    r"^\W*(output|answer|response|return|example|worked example|illustrat)", re.IGNORECASE
)


def sanitise_instructions(text: str) -> str:
    """Drop any section of proposed instructions that is an output format or examples.

    A section is a markdown heading and everything under it up to the next heading of the
    same or a higher level. Applied to every candidate before it is scored, so what is
    selected is what gets exported. The first optimisation run selected instructions that
    embedded three made-up examples (one teaching the wrong convention) and a second output
    format contradicting the fixed one; this is the guard against that.
    """
    kept: list[str] = []
    dropping_level: int | None = None
    for line in text.strip().splitlines():
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if dropping_level is not None and level <= dropping_level:
                dropping_level = None
            if dropping_level is None and _FORBIDDEN_SECTION.match(heading.group(2)):
                dropping_level = level
        if dropping_level is None:
            kept.append(line)
    return "\n".join(kept).strip()


def assemble(frame: Frame, instructions: str, demos: Sequence[Demo] = ()) -> str:
    """The prompt template text for a candidate: fixed head, its instructions and demos, tail."""
    middle = escape_template(sanitise_instructions(instructions))
    if demos:
        middle += "\n\n" + escape_template(render_demos(demos))
    return f"{frame.head}{middle}\n\n{frame.tail}"


# =============================================================================================
# The inputs, rendered exactly as PromptTemplate.render renders them
# =============================================================================================


def case_inputs(request: ClassificationRequest) -> dict[str, str]:
    """The signature's three inputs: session, history (empty if none) and event, as tagged JSON."""
    history = (
        to_tagged_json([e.model_dump(exclude_none=True) for e in request.history])
        if request.history
        else ""
    )
    return {
        "session": to_tagged_json(request.session.model_dump()),
        "history": history,
        "event": to_tagged_json(request.event.model_dump(exclude_none=True)),
    }


def user_message(session: str, history: str, event: str, *, repair: str | None = None) -> str:
    """The user turn :meth:`PromptTemplate.render` builds from the same three inputs."""
    parts = [f"<session>\n{session}\n</session>"]
    if history:
        parts.append(f'<history note="{HISTORY_NOTE}">\n{history}\n</history>')
    parts.append(f"<event>\n{event}\n</event>")
    parts.append("Classify the event.")
    if repair:
        parts.append(repair)
    return "\n\n".join(parts)


DEMO_CONFIDENCE: dict[LabelConfidence, float] = {"high": 0.95, "medium": 0.8, "contested": 0.6}

# =============================================================================================
# The metric
# =============================================================================================

# Score for each outcome, in [0, 1]. A missed violation (gold violation, predicted allow) lets
# the event through: the harm the guardrail exists to stop, so it scores 0. Every other error
# fails safe or costs review: a false block or a `policy_silent` stops the event and a human
# looks, and an `allow` on an undecided case is a softer miss than letting a known violation
# through. Those score 0.25, a quarter of a correct answer, so the optimiser still prefers any
# correct answer to any error but strongly prefers other errors to a missed violation. A
# correct violation earns 0.75 for the verdict and up to 0.25 for citing the right rules.
CORRECT = 1.0
OTHER_ERROR = 0.25
MISSED_VIOLATION = 0.0
VERDICT_SHARE = 0.75


def rule_overlap(gold: Iterable[str], predicted: Iterable[str]) -> float:
    """Jaccard overlap of rule ids; 1.0 when both are empty."""
    g, p = set(gold), set(predicted)
    return len(g & p) / len(g | p) if g | p else 1.0


def score(
    gold: Verdict, gold_rules: Sequence[str], predicted: str, predicted_rules: Sequence[str]
) -> float:
    """Graded score of one prediction (the weights are explained above)."""
    if predicted == gold:
        if gold != "violation":
            return CORRECT
        return VERDICT_SHARE + (CORRECT - VERDICT_SHARE) * rule_overlap(gold_rules, predicted_rules)
    if gold == "violation" and predicted == "allow":
        return MISSED_VIOLATION
    return OTHER_ERROR


def strict_match(
    gold: Verdict, gold_rules: Sequence[str], predicted: str, predicted_rules: Sequence[str]
) -> bool:
    """For choosing demos: right verdict and, for a violation, exactly the right rules."""
    if predicted != gold:
        return False
    return gold != "violation" or set(gold_rules) == set(predicted_rules)


# =============================================================================================
# Train / validation: a holdout grouped by template
# =============================================================================================


def template_of(case: LabeledCase) -> str:
    return next(t for t in case.tags if t.startswith("tpl:"))


def holdout_split(
    cases: Sequence[LabeledCase], *, fraction: float = 0.25, seed: int = 0, min_per_verdict: int = 8
) -> tuple[list[LabeledCase], list[LabeledCase]]:
    """(train, validation), with every template entirely on one side.

    Cases from one template are rephrasings of one idea, so splitting a template would let
    validation reward memorising it. Templates are ordered by a seeded hash and taken until
    the validation side holds ``fraction`` of the cases; the first seed from ``seed`` whose
    validation side has at least ``min_per_verdict`` cases of each verdict is used.
    """
    by_template: dict[str, list[LabeledCase]] = {}
    for case in cases:
        by_template.setdefault(template_of(case), []).append(case)
    for attempt in range(seed, seed + 100):
        order = sorted(
            by_template, key=lambda t: hashlib.sha256(f"{attempt}:{t}".encode()).hexdigest()
        )
        held: set[str] = set()
        size = 0
        for template in order:
            if size >= fraction * len(cases):
                break
            held.add(template)
            size += len(by_template[template])
        val = [c for c in cases if template_of(c) in held]
        if all(sum(c.label == v for c in val) >= min_per_verdict for v in VERDICT_NAMES):
            return [c for c in cases if template_of(c) not in held], val
    raise ValueError("no seed gives a validation set with every verdict")


VERDICT_NAMES: tuple[Verdict, ...] = ("allow", "violation", "policy_silent")


def summarise(scores: Sequence[float], verdicts: Sequence[tuple[str, str]]) -> dict[str, Any]:
    """Mean metric and plain accuracy over (gold, predicted) pairs, for reports."""
    n = len(verdicts)
    correct = sum(g == p for g, p in verdicts)
    missed = sum(g == "violation" and p == "allow" for g, p in verdicts)
    violations = sum(g == "violation" for g, _ in verdicts)
    return {
        "n": n,
        "metric": round(sum(scores) / n, 4) if n else None,
        "accuracy": round(correct / n, 4) if n else None,
        "correct": correct,
        "missed_violations": missed,
        "violations": violations,
    }
