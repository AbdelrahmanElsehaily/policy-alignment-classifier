"""The synthetic data pipeline and the red-team loops: one subcommand per phase.

    uv run python scripts/synthesize.py budget            # spend so far, per provider
    uv run python scripts/synthesize.py calibrate --iteration 1 [--subset]
    ... (see --help)

Reads keys from .env. Every model response is cached under data/synthetic/llm_cache, so a rerun
of any phase is free; every call that reaches a provider is counted in data/synthetic/ledger.json
against the caps in scripts/synthesis_runtime.py. Outputs go to data/synthetic/ (see its README).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
import sys
from collections import Counter

import synthesis_assemble as sa
import synthesis_data as sd
import synthesis_redteam as rtm
import synthesis_report as rep
import synthesis_runtime as rt

from pac.evaluation.infrastructure.case_store import load_cases
from pac.settings import REPO_ROOT, Settings
from pac.synthesis.application.calibration import CheckCase, label_check_set, report
from pac.synthesis.application.prompts import JURY_GUIDE_VERSION, JURY_SYSTEM
from pac.synthesis.domain.records import SyntheticEvent, SynthSplit
from pac.telemetry import configure_logging

logger = logging.getLogger("pac.scripts.synthesize")


# =============================================================================================
# budget
# =============================================================================================


def cmd_budget(args: argparse.Namespace) -> None:
    runtime = rt.Runtime.create()
    if args.reconcile:
        print(f"recovered from the cache: {rt.reconcile(runtime)}")
    data = runtime.ledger.as_dict()
    for provider, totals in data["totals"].items():
        print(f"{provider:<11} calls={totals['calls']:<6} est_usd={totals['usd']:.4f}")
    usage = rt.openrouter_key_usage(runtime.settings)
    print(f"openrouter key usage (provider-reported): ${usage:.4f}")


def check_openrouter(runtime: rt.Runtime, phase: str) -> None:
    """Record the provider-reported OpenRouter spend before a phase; stop at the cap."""
    usage = rt.openrouter_key_usage(runtime.settings)
    with runtime.ledger.phase(phase):
        runtime.ledger.note(openrouter_key_usage_usd=round(usage, 4))
    cap = rt.CAPS["openrouter"].usd or 0.0
    if usage >= cap - 0.15:
        raise SystemExit(f"OpenRouter spend ${usage:.2f} is at the ${cap:.2f} cap: stopping")


# =============================================================================================
# calibrate (guideline 5)
# =============================================================================================


def check_set(*, subset: bool) -> list[CheckCase]:
    """dev (49) and train (257): the only gold labels the pipeline may be tuned on."""
    settings = Settings()
    dev = [c for c in load_cases(settings.cases_path) if c.split == "dev"]
    train = load_cases(settings.train_cases_path)
    cases = [
        CheckCase(
            id=c.id,
            source=c.split,
            request=c.request,
            gold=c.label,
            gold_rules=c.rule_ids,
            gold_confidence=c.label_confidence,
            template=next((t for t in c.tags if t.startswith("tpl:")), ""),
        )
        for c in [*dev, *train]
    ]
    if not subset:
        return cases
    # Iterate on dev plus a fixed, stratified third of train (by label and kind).
    rng = random.Random(7)
    by_stratum: dict[tuple[str, str], list[CheckCase]] = {}
    for case in cases:
        if case.source == "train":
            by_stratum.setdefault((case.gold, case.request.event.kind), []).append(case)
    picked = [c for c in cases if c.source == "dev"]
    for members in by_stratum.values():
        picked += rng.sample(members, max(1, round(len(members) / 3)))
    return picked


async def run_calibration(iteration: int, subset: bool, limit: int | None) -> None:
    runtime = rt.Runtime.create()
    check_openrouter(runtime, f"calibration-{iteration}")
    labeller = runtime.jury()
    cases = check_set(subset=subset)
    if limit:
        cases = cases[:limit]
    with runtime.ledger.phase(f"calibration-{iteration}"):
        results = await label_check_set(
            labeller,
            cases,
            concurrency=3,
            progress=lambda n: (
                logger.info("labelled %d/%d", n, len(cases)) if n % 25 == 0 else None
            ),
        )
    jurors = [j.name for j in labeller.jurors]
    summary = report(results, jurors)
    guide = JURY_SYSTEM.template
    out = {
        "iteration": iteration,
        "subset": subset,
        "guide_version": JURY_GUIDE_VERSION,
        "guide_sha256": hashlib.sha256(guide.encode()).hexdigest()[:12],
        "jurors": {j.name: j.family for j in labeller.jurors},
        "summary": summary,
        "results": [r.model_dump(mode="json") for r in results],
    }
    name = f"iteration-{iteration}{'-subset' if subset else ''}{f'-first{limit}' if limit else ''}"
    rt.write_json(rt.OUT / "calibration" / f"{name}.json", out)
    p = summary["pipeline"]
    print(
        f"iteration {iteration}: n={p['n']} agreement={p['agreement']:.3f} kappa={p['kappa']:.3f}"
    )
    for juror, s in summary["by_juror"].items():
        alone, with_lfs = s["alone"], s["with_lfs"]
        print(
            f"  {juror:<28} alone {alone['agreement'] if alone else float('nan'):.3f}"
            f" (k={alone['kappa'] if alone else float('nan'):.3f})"
            f"  with LFs {with_lfs['agreement'] if with_lfs else float('nan'):.3f}"
            f"  missing={s['missing']}"
        )
    for key in ("by_source", "by_kind", "by_confidence"):
        print(
            f"  {key}: "
            + ", ".join(f"{g}={v['agreement']:.2f}/{v['n']}" for g, v in summary[key].items())
        )
    print(f"  confusion (gold -> predicted): {summary['confusion']}")
    wrong = Counter((r.gold, r.predicted) for r in results if r.gold != r.predicted)
    print(f"  disagreements: {dict(wrong)}")


def cmd_calibrate(args: argparse.Namespace) -> None:
    asyncio.run(run_calibration(args.iteration, args.subset, args.limit))


# =============================================================================================
# seeds (1), record (2), singles (3, 4), label (5), assemble (3, 6, 7), discover (8)
# =============================================================================================


def cmd_seeds(_: argparse.Namespace) -> None:
    runtime = rt.Runtime.create()
    check_openrouter(runtime, "seeds")
    with runtime.ledger.phase("seeds"):
        grown = asyncio.run(sd.seeds(runtime))
    print(f"seeds: {len(grown)} grown, {dict(Counter(s.status for s in grown))}")


def cmd_record(args: argparse.Namespace) -> None:
    runtime = rt.Runtime.create()
    check_openrouter(runtime, "record")
    with runtime.ledger.phase("record"):
        paths = asyncio.run(sd.record(runtime, args.only, parallel=args.parallel, agent=args.agent))
    print(f"recordings: {len(paths)}")


def cmd_singles(args: argparse.Namespace) -> None:
    """Pairs, hard negatives and kept seeds for every split, and grid-fill events if asked."""
    runtime = rt.Runtime.create()
    singles = [s for split in sd.SPLITS for s in sd.singles_from_contrasts(split)]
    singles += sd.singles_from_seeds(sd.load_seeds())
    if args.fill:
        check_openrouter(runtime, "fill")
        events = [e for split in sd.SPLITS for e in sa.load_events(split)]
        with runtime.ledger.phase("fill"):
            plan: tuple[tuple[SynthSplit, int], ...] = (
                ("train", args.fill),
                ("dev", args.fill // 3),
                ("test", args.fill // 3),
            )

            async def fill() -> list[sd.Single]:
                # One event loop for all splits: the backends' semaphores are bound to it.
                made_all: list[sd.Single] = []
                for split, budget in plan:
                    cells = sa.fill_targets(events, split, budget)
                    made = await sd.targeted(runtime, split, cells, rt.CLAUDE)
                    made_all += made
                    print(f"fill {split}: {len(cells)} cells asked, {len(made)} events made")
                return made_all

            singles += asyncio.run(fill())
    rt.write_jsonl(sd.SINGLES, singles)
    print(f"singles: {len(singles)} {dict(Counter((s.split, s.source) for s in singles))}")


def _single_items() -> list[dict[str, object]]:
    items = []
    for s in rt.read_jsonl(sd.SINGLES, sd.Single):
        items.append(
            {
                "id": s.id,
                "split": s.split,
                "source": s.source,
                "template": s.template,
                "request": s.request,
                "rule_hint": s.rule,
                "expected": s.expected,
                "expected_with_history": s.expected_with_history,
                "family": s.family,
                "member": s.member,
            }
        )
    return items


def cmd_label(args: argparse.Namespace) -> None:
    runtime = rt.Runtime.create()
    check_openrouter(runtime, f"label-{args.what}")
    items = sd.conversation_items() if args.what == "conversations" else _single_items()
    done = {i.id for i in sd.load_labelled(sd.LABELS / f"{args.what}.jsonl")}
    todo = [i for i in items if i["id"] not in done]
    print(f"label {args.what}: {len(items)} events, {len(todo)} not yet labelled")
    with runtime.ledger.phase(f"label-{args.what}"):
        asyncio.run(
            sd.label_all(runtime, items, sd.LABELS / f"{args.what}.jsonl", parallel=args.parallel)
        )


def cmd_assemble(_: argparse.Namespace) -> None:
    labelled = [
        *sd.load_labelled(sd.LABELS / "conversations.jsonl"),
        *sd.load_labelled(sd.LABELS / "singles.jsonl"),
    ]
    kept, dropped = sa.dedupe(labelled)
    events = [sd.to_event(i) for i in kept]
    counts = sa.write_splits(events)
    by_split = {s: [e for e in events if e.split == s] for s in sd.SPLITS}
    manifest = sa.write_versions(by_split)
    rt.write_json(sa.REPORTS / "dedup.json", dropped)
    rt.write_json(
        sa.REPORTS / "coverage.json", {s: sa.coverage_report(es) for s, es in by_split.items()}
    )
    rt.write_json(sa.REPORTS / "quality.json", sa.quality_report(by_split, dropped))
    rt.write_json(sa.REPORTS / "leakage.json", sa.leakage_report(by_split))
    rt.write_json(sa.REPORTS / "pairs.json", pair_consistency(labelled))
    sizes = {s: m["enriched"]["n"] for s, m in manifest.items()}
    print(f"splits: {counts}; dropped {len(dropped)}; enriched versions: {sizes}")


def pair_consistency(labelled: list[sd.Labelled]) -> dict[str, object]:
    """CheckList-style directional test: did each pair member get the label its edit predicts?"""
    rows = [i for i in labelled if i.expected is not None and i.source in ("pair", "hard_negative")]
    hits = [i for i in rows if i.consensus.verdict == i.expected]
    hist_rows = [i for i in rows if i.expected_with_history is not None]
    hist_hits = [
        i
        for i in hist_rows
        if (i.consensus_with_history or i.consensus).verdict == i.expected_with_history
    ]
    misses = [
        {
            "id": i.id,
            "family": i.family,
            "member": i.member,
            "expected": i.expected,
            "got": i.consensus.verdict,
            "confidence": i.consensus.confidence,
        }
        for i in rows
        if i.consensus.verdict != i.expected
    ]
    return {
        "single_event": {"n": len(rows), "as_expected": len(hits)},
        "with_history": {"n": len(hist_rows), "as_expected": len(hist_hits)},
        "misses": misses,
    }


def cmd_discover(args: argparse.Namespace) -> None:
    runtime = rt.Runtime.create()
    events = [e for split in sd.SPLITS for e in sa.load_events(split)]
    with runtime.ledger.phase("discover"):
        answers = asyncio.run(sa.classify_events(runtime, events, parallel=args.parallel))
    rt.write_json(sa.REPORTS / "classifier_answers.json", answers)
    queue = sa.review_queue(events, answers)
    n = rt.write_jsonl(rt.OUT / "review_queue.jsonl", queue)
    summary = discovery_summary(events, answers, queue)
    rt.write_json(sa.REPORTS / "discovery.json", summary)
    print(f"review queue: {n}; {summary['overall']}")


def discovery_summary(
    events: list[SyntheticEvent],
    answers: dict[str, list[dict[str, object]]],
    queue: list[dict[str, object]],
) -> dict[str, object]:
    """The classifier against the jury: run-1 agreement, and disagreements that held twice."""

    def stats(rows: list[SyntheticEvent]) -> dict[str, object]:
        first = [answers[e.id][0] for e in rows if e.id in answers]
        agree = sum(1 for e, a in zip(rows, first, strict=False) if a["verdict"] == e.label)
        twice = sum(
            1
            for e in rows
            if len(answers.get(e.id, [])) >= 2
            and all(a["verdict"] != e.label and a["error"] is None for a in answers[e.id])
        )
        missed = sum(
            1
            for e in rows
            if e.label == "violation"
            and len(answers.get(e.id, [])) >= 2
            and all(a["verdict"] == "allow" for a in answers[e.id])
        )
        return {
            "n": len(rows),
            "run1_agreement": round(agree / len(rows), 3) if rows else None,
            "wrong_twice": twice,
            "violations_passed_twice": missed,
            "jury_violations": sum(1 for e in rows if e.label == "violation"),
        }

    by_agent: dict[str, list[SyntheticEvent]] = {}
    for e in events:
        by_agent.setdefault(e.agent_model or f"single:{e.source}", []).append(e)
    return {
        "overall": stats(events),
        "by_split": {s: stats([e for e in events if e.split == s]) for s in sd.SPLITS},
        "by_agent_or_source": {k: stats(v) for k, v in sorted(by_agent.items())},
        "by_confidence": {
            c: stats([e for e in events if e.label_confidence == c])
            for c in ("high", "medium", "contested")
        },
        "review_queue": {
            "rows": len(queue),
            "reasons": dict(Counter(r for row in queue for r in row["reasons"])),  # type: ignore[attr-defined]
        },
    }


# =============================================================================================
# red-team loops
# =============================================================================================


def cmd_redteam(args: argparse.Namespace) -> None:
    runtime = rt.Runtime.create()
    check_openrouter(runtime, f"redteam-{args.loop}")
    with runtime.ledger.phase(f"redteam-{args.loop}"):
        if args.loop == "classifier":
            rtm.classifier_loop(runtime, args.iterations)
        elif args.loop == "policy":
            rtm.policy_loop(runtime, args.iterations)
        else:
            traces = asyncio.run(rtm.enforcement(runtime, args.iterations))
            print(f"guarded conversations: {len(traces)}")
    cmd_summarise(args)


def cmd_summarise(args: argparse.Namespace) -> None:
    if args.loop == "enforcement":
        summary = rtm.summarise_enforcement()
        print({k: summary[k] for k in ("conversations", "events", "gates", "stops", "patterns")})
        return
    summary = rtm.summarise_loop(args.loop)
    keep = ("attempts", "outcomes", "rejected_unrealistic", "successes", "success_rate_of_judged")
    print({k: summary[k] for k in keep}, summary["by_goal"], f"audit={summary['audit_sample']}")


def cmd_report(_: argparse.Namespace) -> None:
    for path in rep.write_all():
        print(f"wrote {path.relative_to(REPO_ROOT)}")


# =============================================================================================
# main
# =============================================================================================


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    bud = sub.add_parser("budget", help="spend so far")
    bud.add_argument("--reconcile", action="store_true", help="count cached calls the ledger lacks")
    bud.set_defaults(func=cmd_budget)
    cal = sub.add_parser("calibrate", help="label dev+train with the jury; agreement and kappa")
    cal.add_argument("--iteration", type=int, required=True)
    cal.add_argument("--subset", action="store_true", help="dev plus a stratified third of train")
    cal.add_argument("--limit", type=int, default=None, help="only the first N cases (smoke test)")
    cal.set_defaults(func=cmd_calibrate)
    sub.add_parser("seeds", help="grow, dedupe and review boundary seeds").set_defaults(
        func=cmd_seeds
    )
    rec = sub.add_parser("record", help="record simulated conversations (never overwrites)")
    rec.add_argument("only", nargs="*", help="brief ids or split names (default: all)")
    rec.add_argument("--parallel", type=int, default=6)
    rec.add_argument("--agent", default="", help="only briefs whose agent model contains this")
    rec.set_defaults(func=cmd_record)
    sng = sub.add_parser("singles", help="pairs, hard negatives, seeds; --fill N for the grid")
    sng.add_argument(
        "--fill", type=int, default=0, help="grid-fill events for train (dev, test: N/3)"
    )
    sng.set_defaults(func=cmd_singles)
    lab = sub.add_parser("label", help="label conversations or singles with LFs + the jury")
    lab.add_argument("what", choices=("conversations", "singles"))
    lab.add_argument("--parallel", type=int, default=6)
    lab.set_defaults(func=cmd_label)
    sub.add_parser("assemble", help="dedupe, splits, versions, reports").set_defaults(
        func=cmd_assemble
    )
    dis = sub.add_parser("discover", help="classifier vs jury; review queue")
    dis.add_argument("--parallel", type=int, default=4)
    dis.set_defaults(func=cmd_discover)
    red = sub.add_parser("redteam", help="run a red-team loop (resumes from its attempts file)")
    red.add_argument("loop", choices=("classifier", "policy", "enforcement"))
    red.add_argument(
        "--iterations", type=int, default=40, help="attempts (conversations for enforcement)"
    )
    red.set_defaults(func=cmd_redteam)
    summ = sub.add_parser("summarise", help="rebuild a loop's archive, rates and audit sample")
    summ.add_argument("loop", choices=("classifier", "policy", "enforcement"))
    summ.set_defaults(func=cmd_summarise)
    sub.add_parser("report", help="Markdown reports from the phases' JSON").set_defaults(
        func=cmd_report
    )
    args = parser.parse_args(argv)
    configure_logging(Settings().log_level, Settings().log_format)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    raise SystemExit(main(sys.argv[1:]))
