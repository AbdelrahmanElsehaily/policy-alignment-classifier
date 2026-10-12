"""Assembly phases of scripts/synthesize.py: dedupe, splits, versions, quality, discovery.

Guidelines 3, 6, 7 and 8: take every labelled event, drop exact and near-duplicates (of the eval
sets, across splits, within a split), write the splits and their two versions (base rate,
enriched), measure coverage, diversity and drift, and build the review queue from jury
disagreements and jury-classifier disagreements.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

import synthesis_runtime as rt
from synthesis_data import SPLITS, Labelled

from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.infrastructure.case_store import load_cases
from pac.guardrail.domain.classification import Classification
from pac.guardrail.domain.decision import VERDICTS
from pac.monitoring.domain.drift import Profile, compare
from pac.monitoring.domain.features import observe_expected
from pac.monitoring.infrastructure.exporters import load_profile
from pac.settings import REPO_ROOT, Settings
from pac.synthesis.domain.dedup import NearDuplicateIndex, event_text
from pac.synthesis.domain.grid import (
    base_rate,
    coverage,
    enriched,
    fill_plan,
    label_mix,
    projections,
)
from pac.synthesis.domain.language import event_language
from pac.synthesis.domain.metrics import distinct_n, length_buckets
from pac.synthesis.domain.records import SyntheticEvent, SynthSplit
from pac.synthesis.domain.specs import BOUNDARY_SPECS
from pac.synthesis.domain.world import build_slice

logger = logging.getLogger("pac.scripts.synthesize")

EVENTS = rt.OUT / "events"
VERSIONS = rt.OUT / "versions"
REPORTS = rt.OUT / "reports"
EVAL_FILES = ("dev_test.jsonl", "stress.jsonl", "agents.jsonl", "agents_pressure.jsonl")
# When two splits share a near-duplicate, the event stays in the split listed first.
PRIORITY = ("test", "dev", "train")


def eval_cases() -> list[LabeledCase]:
    eval_dir = REPO_ROOT / "data" / "eval"
    cases: list[LabeledCase] = []
    for name in EVAL_FILES:
        cases += load_cases(eval_dir / name)
    return cases


def eval_index() -> NearDuplicateIndex:
    """Every eval event and every event in an eval case's history: what synthetic data must
    not repeat or nearly repeat."""
    index = NearDuplicateIndex()
    seen: set[str] = set()
    for case in eval_cases():
        for event in (case.request.event, *case.request.history):
            key = event.model_dump_json()
            if key not in seen:
                seen.add(key)
                index.add(event, case.id)
    return index


def dedupe(items: Sequence[Labelled]) -> tuple[list[Labelled], list[dict[str, Any]]]:
    """Drop events that repeat an eval event, an event of a higher-priority split, or an
    earlier event of the same split. Returns the kept items and a log of the dropped ones."""
    protected = eval_index()
    kept: list[Labelled] = []
    dropped: list[dict[str, Any]] = []
    by_split = {s: [i for i in items if i.split == s] for s in PRIORITY}
    accepted: dict[str, NearDuplicateIndex] = {}
    for split in PRIORITY:
        own = NearDuplicateIndex()
        # Every kept event of this split, contrast members included, for the splits after it.
        # Leaving pairs and hard negatives out let two dev events nearly repeat train ones.
        everything = NearDuplicateIndex()
        # Contrast-set members are minimal edits of each other by design ($199 vs $201, the
        # same card in four positions): they are deduplicated against the eval sets and the
        # other splits, and against the rest of their split, but not against their own family.
        family: dict[str, NearDuplicateIndex] = {}
        for item in sorted(by_split[split], key=lambda i: i.id):
            event = item.request.event
            score, other = protected.closest(event)
            if score >= 0.6:
                dropped.append(
                    {
                        "id": item.id,
                        "split": split,
                        "why": "eval",
                        "overlap": round(score, 3),
                        "with": other,
                    }
                )
                continue
            higher = [(s, accepted[s].closest(event)) for s in accepted]
            clash = next(((s, sc, o) for s, (sc, o) in higher if sc >= 0.6), None)
            if clash is not None:
                dropped.append(
                    {
                        "id": item.id,
                        "split": split,
                        "why": f"split:{clash[0]}",
                        "overlap": round(clash[1], 3),
                        "with": clash[2],
                    }
                )
                continue
            score, other = own.closest(event)
            if score >= 0.6 and item.source in ("pair", "hard_negative"):
                same, _ = family.setdefault(item.template, NearDuplicateIndex()).closest(event)
                if same >= score:
                    score = 0.0  # its closest neighbour is its own contrast partner
            if score >= 0.6:
                dropped.append(
                    {
                        "id": item.id,
                        "split": split,
                        "why": "within-split",
                        "overlap": round(score, 3),
                        "with": other,
                    }
                )
                continue
            if item.source in ("pair", "hard_negative"):
                family.setdefault(item.template, NearDuplicateIndex()).add(event, item.id)
            else:
                own.add(event, item.id)
            everything.add(event, item.id)
            kept.append(item)
        accepted[split] = everything
    return kept, dropped


def write_splits(events: Sequence[SyntheticEvent]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for split in SPLITS:
        rows = sorted((e for e in events if e.split == split), key=lambda e: e.id)
        counts[split] = rt.write_jsonl(EVENTS / f"{split}.jsonl", rows)
    return counts


def load_events(split: str) -> list[SyntheticEvent]:
    return rt.read_jsonl(EVENTS / f"{split}.jsonl", SyntheticEvent)


def as_labeled_case(event: SyntheticEvent, suffix: str = "") -> dict[str, Any]:
    """The event in the eval sets' format, so `optimize_prompt.py --extra-train` can read it."""
    case = LabeledCase(
        id=f"{event.id}{suffix}",
        split="train",
        request=event.request,
        label=event.label,
        rule_ids=event.rule_ids,
        label_confidence=event.label_confidence,
        rationale=event.rationale,
        tags=("synthetic", f"source:{event.source}", f"tpl:{event.template}", *event.tags),
        origin="transcript" if event.source == "conversation" else "targeted",
        label_with_history=event.label_with_history if event.request.history else None,
        rule_ids_with_history=event.rule_ids_with_history if event.request.history else (),
    )
    return case.model_dump(mode="json")


def write_versions(events_by_split: dict[SynthSplit, list[SyntheticEvent]]) -> dict[str, Any]:
    manifest: dict[str, Any] = {}
    for split, events in events_by_split.items():
        realistic = base_rate(events)
        balanced = enriched(events, cap=12)
        manifest[split] = {
            "base_rate": {"n": len(realistic), "label_mix": label_mix(e.label for e in realistic)},
            "enriched": {
                "n": sum(c for _, c in balanced),
                "distinct": len(balanced),
                "oversampled": sum(1 for _, c in balanced if c > 1),
                "label_mix": label_mix(e.label for e, c in balanced for _ in range(c)),
            },
        }
        rt.write_jsonl(
            VERSIONS / f"{split}_base_rate.jsonl", (as_labeled_case(e) for e in realistic)
        )
        rows = [
            as_labeled_case(e, "" if k == 0 else f"-copy{k + 1}")
            for e, c in balanced
            for k in range(c)
        ]
        rt.write_jsonl(VERSIONS / f"{split}_enriched.jsonl", rows)
    rt.write_json(VERSIONS / "manifest.json", manifest)
    return manifest


# =============================================================================================
# Reports: coverage (3), quality and drift (7)
# =============================================================================================


def coverage_report(events: Sequence[SyntheticEvent]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for projection in projections(BOUNDARY_SPECS):
        cover = coverage((e.cell for e in events), projection)
        out[projection.name] = {
            "valid_cells": len(projection.valid),
            "filled": cover.filled,
            "share": round(cover.share, 3),
            "empty": ["|".join(k) for k in cover.empty()],
            "counts": {"|".join(k): v for k, v in sorted(cover.counts.items())},
        }
    return out


def fill_targets(
    events: Sequence[SyntheticEvent], split: str, budget: int
) -> list[tuple[str, ...]]:
    primary = projections(BOUNDARY_SPECS)[0]
    cover = coverage((e.cell for e in events if e.split == split), primary)
    return fill_plan(cover, target=2, budget=budget)


def _texts(events: Iterable[SyntheticEvent]) -> list[str]:
    return [event_text(e.request.event) for e in events]


def quality_report(
    events_by_split: dict[SynthSplit, list[SyntheticEvent]], dropped: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    settings = Settings()
    reference = load_profile(settings.monitoring_reference_path)
    test_mix = label_mix(c.label for c in load_cases(settings.cases_path) if c.split == "test")
    out: dict[str, Any] = {
        "dedup": {"dropped": len(dropped), "by_reason": dict(Counter(d["why"] for d in dropped))}
    }
    out["test_split_label_mix"] = test_mix
    for split, events in events_by_split.items():
        texts = _texts(events)
        by_template = Counter(e.template for e in events)
        profile = Profile.of(
            observe_expected(e.request.without_history(), e.label, e.rule_ids) for e in events
        )
        drift = compare(reference, profile, min_events=30) if reference is not None else None
        out[split] = {
            "events": len(events),
            "label_mix": label_mix(e.label for e in events),
            "label_mix_vs_test": {
                v: round(label_mix(e.label for e in events)[v] - test_mix[v], 3) for v in VERDICTS
            },
            "confidence": dict(Counter(e.label_confidence for e in events)),
            "sources": dict(Counter(e.source for e in events)),
            "kinds": dict(Counter(e.request.event.kind for e in events)),
            "agents": dict(Counter(e.agent_model or "-" for e in events)),
            "templates": len(by_template),
            "events_per_template": {
                "max": max(by_template.values(), default=0),
                "mean": round(len(events) / max(1, len(by_template)), 2),
            },
            "distinct_1": round(distinct_n(texts, 1), 3),
            "distinct_2": round(distinct_n(texts, 2), 3),
            "distinct_3": round(distinct_n(texts, 3), 3),
            "languages": dict(Counter(event_language(e.request.event) for e in events)),
            "lengths": length_buckets(texts),
            "drift": None
            if drift is None
            else {
                "status": drift.status,
                "features": {
                    f.feature: {"psi": f.psi, "status": f.status, "movers": f.movers, "new": f.new}
                    for f in drift.features
                },
            },
        }
    return out


# =============================================================================================
# Leakage (guideline 6)
# =============================================================================================


def world_ids(split: str) -> set[str]:
    return build_slice(split).identifiers()  # type: ignore[arg-type]


def leakage_report(events_by_split: dict[SynthSplit, list[SyntheticEvent]]) -> dict[str, Any]:
    eval_text = "\n".join(c.model_dump_json() for c in eval_cases())
    out: dict[str, Any] = {}
    index = eval_index()
    for split, events in events_by_split.items():
        others = [s for s in events_by_split if s != split]
        text = "\n".join(e.model_dump_json() for e in events)
        other_ids = set().union(*(world_ids(o) for o in others)) if others else set()
        out[split] = {
            "eval_ids_in_split": sorted(i for i in world_ids(split) if i in eval_text),
            "other_split_ids_in_split": sorted(i for i in other_ids if i in text),
            "near_duplicates_of_eval": sum(
                1 for e in events if index.near_duplicate(e.request.event) is not None
            ),
            "templates_shared": sorted(
                {e.template for e in events}
                & {e.template for o in others for e in events_by_split[o]}
            ),
            "agent_models": sorted({e.agent_model for e in events if e.agent_model}),
        }
    return out


# =============================================================================================
# Discovery (guideline 8)
# =============================================================================================


async def classify_events(
    runtime: rt.Runtime, events: Sequence[SyntheticEvent], *, parallel: int = 4
) -> dict[str, list[dict[str, Any]]]:
    """The target classifier's answer on each event alone (run 1); where it disagrees with the
    jury, a second run, so a disagreement counts only if it holds twice."""
    first, second = runtime.classifier(1), runtime.classifier(2)
    semaphore = asyncio.Semaphore(parallel)
    out: dict[str, list[dict[str, Any]]] = {}

    def answer(c: Classification) -> dict[str, Any]:
        d = c.decision
        return {
            "verdict": d.verdict,
            "rule_ids": list(d.rule_ids),
            "confidence": d.confidence,
            "error": c.error,
            "rationale": d.rationale[:300],
        }

    async def one(event: SyntheticEvent) -> None:
        request = event.request.without_history()
        async with semaphore:
            runs = [answer(await first.classify(request))]
            if runs[0]["verdict"] != event.label and runs[0]["error"] is None:
                runs.append(answer(await second.classify(request)))
        out[event.id] = runs

    await asyncio.gather(*(one(e) for e in events))
    return out


def review_queue(
    events: Sequence[SyntheticEvent], answers: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    queue = []
    for e in events:
        reasons = []
        if e.label_confidence != "high":
            reasons.append(f"jury-{e.label_confidence}")
        runs = answers.get(e.id, [])
        if len(runs) >= 2 and all(r["verdict"] != e.label and r["error"] is None for r in runs):
            reasons.append("jury-vs-classifier")
        lf_dissent = [
            r
            for r, v in e.lf.items()
            if any(jv != e.label for jv in e.votes.values()) and v != "allow"
        ]
        if not reasons:
            continue
        queue.append(
            {
                "id": e.id,
                "split": e.split,
                "reasons": reasons,
                "kind": e.request.event.kind,
                "session": e.request.session.model_dump(),
                "event": e.request.event.model_dump(exclude_none=True),
                "history_tail": [h.model_dump(exclude_none=True) for h in e.request.history[-3:]],
                "jury_label": e.label,
                "jury_rules": list(e.rule_ids),
                "jury_confidence": e.label_confidence,
                "jury_votes": e.votes,
                "lf": e.lf,
                "lf_rules_with_dissent": lf_dissent,
                "rationale": e.rationale,
                "classifier_runs": runs,
                "cell": e.cell.model_dump(),
                "source": e.source,
                "template": e.template,
            }
        )
    return queue
