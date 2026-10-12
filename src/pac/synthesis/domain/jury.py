"""A jury's per-rule ballots turned into one label (guideline 5).

Each juror labels the event against every rule that can judge its kind: ``allow``,
``violation`` or ``silent`` (with the missing fact or the conflict named). The labelling
functions decide the mechanical rules outright; the jury decides the rest by majority. The
event's verdict follows from the rules: any violation makes it a violation, otherwise any
silent rule makes it ``policy_silent``, otherwise ``allow``.

Confidence comes from agreement. Each juror's own event verdict (its ballot, with the labelling
functions applied) is compared with the consensus: ``high`` when every juror agrees, ``medium``
on a majority, ``contested`` otherwise. A label decided by a labelling function of lower
confidence (a contested reading such as publishable keys) carries that confidence.

``policy_silent`` needs a reason: a juror's ``silent`` with neither a missing fact nor a conflict
is counted as ``allow``, as the policy says ("Do not invent rules to fill the gap").
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import Field

from pac.guardrail.domain.decision import Verdict
from pac.guardrail.domain.value_object import ValueObject
from pac.synthesis.domain.labelling import LabelConfidence, RuleLabel, RuleVerdict

_RANK: dict[LabelConfidence, int] = {"high": 2, "medium": 1, "contested": 0}


class RuleVote(ValueObject):
    rule_id: str
    verdict: RuleVerdict
    reason: str = ""
    missing_fact: str = ""
    conflict: str = ""

    def normalised(self) -> RuleVote:
        """A ``silent`` vote with no missing fact and no conflict counts as ``allow``."""
        if self.verdict == "silent" and not (self.missing_fact.strip() or self.conflict.strip()):
            return self.model_copy(update={"verdict": "allow"})
        return self


class Ballot(ValueObject):
    """One juror's labels for one event. ``error`` is set when its answer was unusable."""

    juror: str
    family: str
    votes: tuple[RuleVote, ...] = ()
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.error is None and bool(self.votes)

    def vote(self, rule_id: str) -> RuleVote | None:
        return next((v.normalised() for v in self.votes if v.rule_id == rule_id), None)


class RuleOutcome(ValueObject):
    rule_id: str
    verdict: RuleVerdict
    confidence: LabelConfidence
    source: Literal["lf", "jury"]
    reason: str
    votes: dict[str, RuleVerdict] = Field(default_factory=dict)


class Consensus(ValueObject):
    verdict: Verdict
    rule_ids: tuple[str, ...]
    confidence: LabelConfidence
    rules: tuple[RuleOutcome, ...]
    juror_verdicts: dict[str, Verdict]
    rationale: str
    # The jury had no majority on the event verdict and no labelling function decided it.
    unresolved: bool = False
    # Every valid juror gave the consensus verdict.
    unanimous: bool = False

    def outcome(self, rule_id: str) -> RuleOutcome | None:
        return next((r for r in self.rules if r.rule_id == rule_id), None)


def _rule_key(rule_id: str) -> int:
    return int(rule_id[1:])


def event_verdict(rules: Mapping[str, RuleVerdict]) -> tuple[Verdict, tuple[str, ...]]:
    """The event's verdict from its rules' verdicts, and the rules a violation rests on."""
    broken = tuple(sorted((r for r, v in rules.items() if v == "violation"), key=_rule_key))
    if broken:
        return "violation", broken
    if any(v == "silent" for v in rules.values()):
        return "policy_silent", ()
    return "allow", ()


def juror_verdict(
    ballot: Ballot, applicable: Sequence[str], lfs: Mapping[str, RuleLabel]
) -> Verdict:
    """What this juror's ballot says about the event once the labelling functions apply."""
    rules: dict[str, RuleVerdict] = {}
    for rule_id in applicable:
        if rule_id in lfs:
            rules[rule_id] = lfs[rule_id].verdict
            continue
        vote = ballot.vote(rule_id)
        rules[rule_id] = vote.verdict if vote is not None else "allow"
    return event_verdict(rules)[0]


def aggregate(
    ballots: Sequence[Ballot], applicable: Sequence[str], lfs: Mapping[str, RuleLabel]
) -> Consensus:
    """One label from the jury's ballots and the labelling functions."""
    valid = [b for b in ballots if b.valid]
    outcomes: list[RuleOutcome] = []
    for rule_id in sorted(set(applicable) | set(lfs), key=_rule_key):
        votes: dict[str, RuleVerdict] = {}
        for ballot in valid:
            vote = ballot.vote(rule_id)
            if vote is not None:
                votes[ballot.juror] = vote.verdict
        if rule_id in lfs:
            lf = lfs[rule_id]
            outcomes.append(
                RuleOutcome(
                    rule_id=rule_id,
                    verdict=lf.verdict,
                    confidence=lf.confidence,
                    source="lf",
                    reason=f"{lf.source}: {lf.reason}"
                    + (f" (missing: {lf.missing_fact})" if lf.missing_fact else ""),
                    votes=votes,
                )
            )
            continue
        outcomes.append(_jury_rule(rule_id, votes, valid))
    rules = {o.rule_id: o.verdict for o in outcomes}
    verdict, broken = event_verdict(rules)
    juror_verdicts = {b.juror: juror_verdict(b, applicable, lfs) for b in valid}
    agree = sum(1 for v in juror_verdicts.values() if v == verdict)
    deciding = [o for o in outcomes if _decides(o, verdict, broken)]
    lf_decided = bool(deciding) and all(o.source == "lf" for o in deciding)
    if not valid:
        confidence: LabelConfidence = "medium" if lf_decided else "contested"
    elif agree == len(valid) and len(valid) >= 2:
        confidence = "high"
    elif agree * 2 > len(valid):
        confidence = "medium"
    else:
        confidence = "contested"
    if lf_decided:
        # A rule the labelling functions decide is not up for a vote: the jurors' dissent on
        # it doesn't lower confidence, but the function's own confidence caps it.
        confidence = min((o.confidence for o in deciding), key=lambda c: _RANK[c])
    elif deciding:
        confidence = min(
            [confidence, *(o.confidence for o in deciding if o.source == "lf")],
            key=lambda c: _RANK[c],
        )
    unresolved = bool(valid) and agree * 2 <= len(valid) and not lf_decided
    return Consensus(
        verdict=verdict,
        rule_ids=broken,
        confidence=confidence,
        rules=tuple(outcomes),
        juror_verdicts=juror_verdicts,
        rationale=_rationale(verdict, outcomes, broken),
        unresolved=unresolved,
        unanimous=bool(valid) and agree == len(valid),
    )


def _decides(outcome: RuleOutcome, verdict: Verdict, broken: Sequence[str]) -> bool:
    if verdict == "violation":
        return outcome.rule_id in broken
    if verdict == "policy_silent":
        return outcome.verdict == "silent"
    return False


def _jury_rule(
    rule_id: str, votes: Mapping[str, RuleVerdict], valid: Sequence[Ballot]
) -> RuleOutcome:
    if not votes:
        return RuleOutcome(
            rule_id=rule_id,
            verdict="allow",
            confidence="contested",
            source="jury",
            reason="no votes",
        )
    counts = Counter(votes.values())
    top, n = counts.most_common(1)[0]
    tied = [v for v, c in counts.items() if c == n]
    if len(tied) > 1:
        # No majority on this rule: the middle reading (silent) when it is among them,
        # otherwise allow, and contested either way.
        top = "silent" if "silent" in tied else "allow"
        confidence: LabelConfidence = "contested"
    elif n == len(votes):
        confidence = "high"
    elif n * 2 > len(votes):
        confidence = "medium"
    else:
        confidence = "contested"
    reasons = []
    for ballot in valid:
        vote = ballot.vote(rule_id)
        if vote is not None and vote.verdict == top:
            detail = vote.missing_fact or vote.conflict
            reasons.append(vote.reason + (f" (missing/conflict: {detail})" if detail else ""))
    return RuleOutcome(
        rule_id=rule_id,
        verdict=top,
        confidence=confidence,
        source="jury",
        reason=reasons[0] if reasons else "",
        votes=dict(votes),
    )


def _rationale(verdict: Verdict, outcomes: Sequence[RuleOutcome], broken: Sequence[str]) -> str:
    if verdict == "violation":
        parts = [f"{o.rule_id}: {o.reason}" for o in outcomes if o.rule_id in broken]
    elif verdict == "policy_silent":
        parts = [f"{o.rule_id} silent: {o.reason}" for o in outcomes if o.verdict == "silent"]
    else:
        parts = ["no rule broken"]
    return "; ".join(p.strip() for p in parts)[:1500] or "no reason given"
