"""Jury aggregation (per rule, labelling functions first) and the agreement statistics."""

from __future__ import annotations

import pytest

from pac.synthesis.domain.jury import Ballot, RuleVote, aggregate, event_verdict
from pac.synthesis.domain.labelling import RuleLabel
from pac.synthesis.domain.metrics import (
    agreement,
    cohen_kappa,
    distinct_n,
    fleiss_kappa,
    length_buckets,
    wilson,
)

RULES = ("R9", "R10", "R11")


def ballot(name: str, **verdicts: str) -> Ballot:
    votes = []
    for rule in RULES:
        v = verdicts.get(rule, "allow")
        votes.append(
            RuleVote(
                rule_id=rule,
                verdict=v,
                reason="r",
                missing_fact="the fact" if v == "silent" else "",
            )
        )
    return Ballot(juror=name, family=name, votes=tuple(votes))


def test_event_verdict_from_rule_verdicts() -> None:
    assert event_verdict({"R1": "allow", "R2": "silent"}) == ("policy_silent", ())
    assert event_verdict({"R5": "violation", "R1": "violation", "R2": "silent"}) == (
        "violation",
        ("R1", "R5"),
    )
    assert event_verdict({"R1": "allow"}) == ("allow", ())


def test_unanimous_jury_is_high_confidence() -> None:
    c = aggregate(
        [ballot("a", R9="violation"), ballot("b", R9="violation"), ballot("c", R9="violation")],
        RULES,
        {},
    )
    assert (c.verdict, c.rule_ids, c.confidence, c.unanimous) == (
        "violation",
        ("R9",),
        "high",
        True,
    )


def test_majority_is_medium_and_split_is_contested() -> None:
    c = aggregate(
        [ballot("a", R9="violation"), ballot("b", R9="violation"), ballot("c")], RULES, {}
    )
    assert (c.verdict, c.confidence, c.unanimous) == ("violation", "medium", False)
    split = aggregate(
        [ballot("a", R9="violation"), ballot("b", R10="silent"), ballot("c")], RULES, {}
    )
    assert split.confidence == "contested"


def test_silent_without_a_named_fact_or_conflict_counts_as_allow() -> None:
    bare = Ballot(
        juror="a",
        family="a",
        votes=(
            RuleVote(rule_id="R9", verdict="silent"),
            RuleVote(rule_id="R10", verdict="allow"),
            RuleVote(rule_id="R11", verdict="allow"),
        ),
    )
    c = aggregate([bare, ballot("b"), ballot("c")], RULES, {})
    assert c.verdict == "allow"
    assert c.confidence == "high"


def test_labelling_functions_override_the_jury_on_their_rules() -> None:
    lf = {
        "R5": RuleLabel(rule_id="R5", verdict="violation", reason="over $200", source="lf_r5_cap")
    }
    rules = ("R5", "R8")

    def b(name: str, r5: str) -> Ballot:
        return Ballot(
            juror=name,
            family=name,
            votes=(
                RuleVote(rule_id="R5", verdict=r5, missing_fact="x"),
                RuleVote(rule_id="R8", verdict="allow"),
            ),
        )

    c = aggregate([b("a", "silent"), b("b", "silent"), b("c", "allow")], rules, lf)
    assert (c.verdict, c.rule_ids, c.confidence) == ("violation", ("R5",), "high")
    outcome = c.outcome("R5")
    assert outcome is not None
    assert outcome.source == "lf"


def test_a_contested_labelling_function_caps_confidence() -> None:
    lf = {
        "R4": RuleLabel(
            rule_id="R4",
            verdict="silent",
            confidence="contested",
            reason="pk",
            missing_fact="?",
            source="lf",
        )
    }
    c = aggregate([ballot("a"), ballot("b"), ballot("c")], RULES, lf)
    assert (c.verdict, c.confidence) == ("policy_silent", "contested")


def test_failed_ballots_are_ignored() -> None:
    c = aggregate(
        [
            ballot("a", R9="violation"),
            ballot("b", R9="violation"),
            Ballot(juror="c", family="c", error="x"),
        ],
        RULES,
        {},
    )
    assert (c.verdict, c.confidence) == ("violation", "high")
    assert set(c.juror_verdicts) == {"a", "b"}


def test_agreement_and_kappa() -> None:
    a = ["allow", "allow", "violation", "policy_silent"]
    assert agreement(a, a) == 1.0
    assert cohen_kappa(a, a) == 1.0
    assert cohen_kappa(["allow", "violation"] * 5, ["violation", "allow"] * 5) == pytest.approx(
        -1.0
    )
    # The textbook example: 20 items, two raters, observed 0.7, expected 0.5 -> kappa 0.4.
    r1 = ["y"] * 10 + ["n"] * 10
    r2 = ["y"] * 7 + ["n"] * 3 + ["y"] * 3 + ["n"] * 7
    assert cohen_kappa(r1, r2) == pytest.approx(0.4)


def test_fleiss_kappa_is_one_for_perfect_agreement_and_low_for_noise() -> None:
    assert fleiss_kappa([["a", "a", "a"], ["b", "b", "b"]]) == pytest.approx(1.0)
    assert fleiss_kappa([["a", "b", "c"], ["b", "c", "a"], ["c", "a", "b"]]) < 0


def test_diversity_metrics() -> None:
    assert distinct_n(["a b c", "a b c"], 1) == pytest.approx(0.5)
    assert distinct_n(["a b c", "d e f"], 2) == 1.0
    assert length_buckets(["x" * 10, "x" * 200]) == {
        "<50": 1,
        "50-150": 0,
        "150-400": 1,
        "400-1000": 0,
        "1000+": 0,
    }
    low, high = wilson(9, 10)
    assert low < 0.9 < high
