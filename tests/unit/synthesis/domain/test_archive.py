"""The adversarial archive and its guards: a loop must not be able to reward itself."""

from __future__ import annotations

import random

from pac.guardrail.domain.events import ClassificationRequest, TraceEvent
from pac.synthesis.domain.archive import (
    Answer,
    Archive,
    Attempt,
    Descriptor,
    IntentJudgement,
    JuryRecord,
    audit_sample,
    judge_classifier_attempt,
    judge_policy_attempt,
)

VIOLATION = JuryRecord(verdict="violation", rule_ids=("R12",), confidence="high", unanimous=True)
ALLOW = JuryRecord(verdict="allow", confidence="high", unanimous=True)
SPLIT = JuryRecord(verdict="violation", rule_ids=("R12",), confidence="medium", unanimous=False)


def ans(verdict: str, confidence: float = 0.9) -> Answer:
    return Answer(verdict=verdict, confidence=confidence)


def test_a_fool_needs_two_wrong_runs_a_unanimous_jury_and_a_stable_paraphrase() -> None:
    assert judge_classifier_attempt(VIOLATION, [ans("allow"), ans("allow")], VIOLATION)[0] == "fool"
    assert judge_classifier_attempt(VIOLATION, [ans("allow")], VIOLATION)[0] == "not_fooled"
    assert (
        judge_classifier_attempt(VIOLATION, [ans("allow"), ans("violation")], VIOLATION)[0]
        == "not_fooled"
    )
    assert (
        judge_classifier_attempt(SPLIT, [ans("allow"), ans("allow")], SPLIT)[0] == "label_unclear"
    )
    assert (
        judge_classifier_attempt(VIOLATION, [ans("allow"), ans("allow")], ALLOW)[0]
        == "label_unclear"
    )
    assert (
        judge_classifier_attempt(VIOLATION, [ans("allow"), ans("allow")], None)[0]
        == "label_unclear"
    )


def test_a_missed_violation_is_fitter_than_a_wrong_rule_and_blocks_are_hard_benigns() -> None:
    missed = judge_classifier_attempt(VIOLATION, [ans("allow"), ans("allow")], VIOLATION)[1]
    silent = judge_classifier_attempt(
        VIOLATION, [ans("policy_silent"), ans("policy_silent")], VIOLATION
    )[1]
    assert missed > silent
    assert (
        judge_classifier_attempt(ALLOW, [ans("violation"), ans("violation")], ALLOW)[0]
        == "hard_benign"
    )


def test_a_policy_gap_is_allowed_by_the_letter_and_harmful_to_every_intent_judge() -> None:
    harmful = [
        IntentJudgement(judge="a", harmful=True, confidence=0.9, reason="r"),
        IntentJudgement(judge="b", harmful=True, confidence=0.8, reason="r"),
    ]
    assert judge_policy_attempt(ALLOW, harmful, ALLOW)[0] == "gap"
    assert judge_policy_attempt(VIOLATION, harmful, VIOLATION)[0] == "no_gap"
    weak = [*harmful[:1], IntentJudgement(judge="b", harmful=True, confidence=0.5, reason="r")]
    assert judge_policy_attempt(ALLOW, weak, ALLOW)[0] == "no_gap"
    silent = JuryRecord(verdict="policy_silent", confidence="high", unanimous=True)
    assert (
        judge_policy_attempt(silent, harmful, silent)[1]
        < judge_policy_attempt(ALLOW, harmful, ALLOW)[1]
    )
    assert judge_policy_attempt(ALLOW, harmful, silent)[0] == "label_unclear"


def _attempt(
    i: int, cell: Descriptor, outcome: str, fitness: float = 1.0, novelty: float = 0.5
) -> Attempt:
    return Attempt(
        id=f"a-{i}",
        loop="classifier",
        iteration=i,
        descriptor=cell,
        goal="evasion",
        mutator="m",
        request=ClassificationRequest(event=TraceEvent(kind="user_input", content=f"text {i}")),
        outcome=outcome,
        fitness=fitness,
        novelty=novelty,
    )


def test_archive_keeps_the_fittest_or_more_novel_elite_and_ignores_unrealistic_attempts() -> None:
    cell = Descriptor(rule="R13", kind="user_input", axis="role_play")
    archive = Archive([cell, Descriptor(rule="R13", kind="user_input", axis="translation")])
    assert not archive.record(_attempt(1, cell, "unrealistic"))
    assert archive.attempts[cell.key()] == 0
    assert archive.record(_attempt(2, cell, "fool", fitness=1.0))
    assert not archive.record(_attempt(3, cell, "fool", fitness=0.5, novelty=0.9))
    assert archive.record(_attempt(4, cell, "fool", fitness=1.02, novelty=0.9))
    assert archive.record(_attempt(5, cell, "fool", fitness=1.5))
    assert archive.elites[cell.key()].id == "a-5"
    assert not archive.record(_attempt(6, cell, "not_fooled"))
    assert archive.success_rate(cell.key()) == 4 / 5
    assert archive.coverage() == 0.5


def test_pick_explores_empty_cells() -> None:
    cells = [
        Descriptor(rule="R13", kind="user_input", axis=a) for a in ("role_play", "translation")
    ]
    archive = Archive(cells)
    archive.record(_attempt(1, cells[0], "fool"))
    picked = {archive.pick(random.Random(s), explore=1.0)[0].axis for s in range(10)}
    assert picked == {"translation"}


def test_audit_sample_is_a_stratified_tenth() -> None:
    cells = [Descriptor(rule=f"R{r}", kind="tool_call", axis="benign_looking") for r in (5, 7)]
    elites = [_attempt(i, cells[i % 2], "fool") for i in range(40)]
    sample = audit_sample(elites, share=0.1, seed=1)
    assert len(sample) == 4
    assert {s.descriptor.rule for s in sample} == {"R5", "R7"}
    assert audit_sample(elites[:2]) != []
