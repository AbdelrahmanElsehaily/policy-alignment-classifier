# ruff: noqa: E501 -- report tables are long lines of Markdown.
"""Markdown reports of scripts/synthesize.py, built only from the JSON the phases wrote.

uv run python scripts/synthesize.py report
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import synthesis_runtime as rt

from pac.synthesis.domain.metrics import agreement, cohen_kappa

REPORTS = rt.OUT / "reports"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def calibration_md() -> str:
    files = sorted((rt.OUT / "calibration").glob("iteration-*.json"))
    runs = [_load(f) for f in files]
    lines = [
        "# Calibration of the labelling pipeline",
        "",
        "Check set: `data/eval/dev_test.jsonl` split `dev` (49) and `data/train/train_cases.jsonl` "
        "(257). The test split, the stress set and the agents set were never used. Each "
        "iteration changed the jury guide (`JURY_SYSTEM` in `src/pac/synthesis/application/"
        "prompts.py`) and/or the labelling functions; iterations 1 and 2 were run on a fixed "
        "subset (dev plus a stratified third of train), iteration 3 on the full set with the "
        "iteration-2 guide unchanged.",
        "",
        "| Iteration | Guide | Cases | Verdict agreement | Cohen's kappa | MiniMax alone | Claude alone | GPT alone | Jury Fleiss kappa |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        s = run["summary"]
        jurors = s["by_juror"]

        def alone(prefix: str, jurors: dict[str, Any] = jurors) -> str:
            name = next((j for j in jurors if prefix in j), None)
            a = jurors[name]["alone"] if name else None
            return f"{_pct(a['agreement'])} (k {a['kappa']:.2f})" if a else "-"

        lines.append(
            f"| {run['iteration']}{' (subset)' if run['subset'] else ''} | {run['guide_version']} | "
            f"{s['pipeline']['n']} | {_pct(s['pipeline']['agreement'])} | "
            f"{s['pipeline']['kappa']:.3f} | {alone('MiniMax')} | {alone('claude')} | "
            f"{alone('gpt')} | {s['jury_fleiss_kappa_alone']:.3f} |"
        )
    full = next((r for r in runs if not r["subset"]), None)
    subset = next((r for r in runs if r["subset"]), None)
    if full and subset:
        tuned = {r["id"] for r in subset["results"]}
        held = [r for r in full["results"] if r["id"] not in tuned]
        gold = [r["gold"] for r in held]
        pred = [r["predicted"] for r in held]
        lines += [
            "",
            f"**Never looked at while tuning** (the {len(held)} train events outside the "
            f"iteration subset): agreement {_pct(agreement(gold, pred))}, kappa "
            f"{cohen_kappa(gold, pred):.3f}. Iterating on the subset did not overfit it.",
        ]
        s = full["summary"]
        lines += [
            "",
            "## Final iteration in detail",
            "",
            "| Slice | n | agreement | kappa |",
            "|---|---|---|---|",
        ]
        for key in ("by_source", "by_kind", "by_confidence"):
            for group, v in s[key].items():
                lines.append(
                    f"| {key[3:]} = {group} | {v['n']} | {_pct(v['agreement'])} | {v['kappa']:.3f} |"
                )
        lines += [
            "",
            "Confusion (rows gold, columns predicted):",
            "",
            "| gold \\ pred | allow | violation | policy_silent |",
            "|---|---|---|---|",
        ]
        for g, row in s["confusion"].items():
            lines.append(f"| {g} | {row['allow']} | {row['violation']} | {row['policy_silent']} |")
        wrong = [r for r in full["results"] if r["gold"] != r["predicted"]]
        lines += ["", f"Remaining disagreements ({len(wrong)}):", ""]
        for r in wrong:
            lines.append(
                f"- `{r['id']}` ({r['source']}, {r['kind']}): gold {r['gold']}, pipeline {r['predicted']} ({r['confidence']}): {r['rationale'][:160]}"
            )
    return "\n".join(lines) + "\n"


def coverage_md() -> str:
    cover = _load(REPORTS / "coverage.json") or {}
    lines = [
        "# Coverage grid",
        "",
        "Share of valid cells with at least one event, per split and projection.",
        "",
        "| Projection | " + " | ".join(cover) + " |",
        "|---|" + "---|" * len(cover),
    ]
    names: list[str] = list(next(iter(cover.values()), {}).keys()) if cover else []
    for name in names:
        cells = [
            f"{cover[s][name]['filled']}/{cover[s][name]['valid_cells']} ({_pct(cover[s][name]['share'])})"
            for s in cover
        ]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    for split, proj in cover.items():
        empty = proj["kind x rule x verdict"]["empty"]
        lines += [
            "",
            f"**{split}: empty primary cells ({len(empty)})**: "
            + ", ".join(f"`{e}`" for e in empty[:60]),
        ]
    return "\n".join(lines) + "\n"


def quality_md() -> str:
    q = _load(REPORTS / "quality.json") or {}
    leak = _load(REPORTS / "leakage.json") or {}
    pairs = _load(REPORTS / "pairs.json") or {}
    splits = [s for s in ("train", "dev", "test") if s in q]
    lines = ["# Quality, diversity, drift and leakage", ""]
    lines += [
        f"Deduplication dropped {q.get('dedup', {}).get('dropped', 0)} events: {q.get('dedup', {}).get('by_reason', {})}.",
        "",
    ]
    lines += [
        "| | " + " | ".join(splits) + " | eval test split |",
        "|---|" + "---|" * (len(splits) + 1),
    ]

    def row(name: str, fn: Any, ref: str = "") -> None:
        lines.append(f"| {name} | " + " | ".join(str(fn(q[s])) for s in splits) + f" | {ref} |")

    test_mix = q.get("test_split_label_mix", {})
    row("events", lambda x: x["events"])
    row(
        "allow / violation / silent",
        lambda x: " / ".join(
            _pct(x["label_mix"][v]) for v in ("allow", "violation", "policy_silent")
        ),
        " / ".join(_pct(test_mix.get(v, 0)) for v in ("allow", "violation", "policy_silent")),
    )
    row("label confidence", lambda x: x["confidence"])
    row("sources", lambda x: x["sources"])
    row("agent models", lambda x: x["agents"])
    row(
        "templates (events per template, mean / max)",
        lambda x: (
            f"{x['templates']} ({x['events_per_template']['mean']} / {x['events_per_template']['max']})"
        ),
    )
    row(
        "distinct-1 / -2 / -3",
        lambda x: f"{x['distinct_1']} / {x['distinct_2']} / {x['distinct_3']}",
    )
    row("languages", lambda x: x["languages"])
    row("lengths (chars)", lambda x: x["lengths"])
    lines += [
        "",
        "## Drift against the eval reference (`results/monitoring/reference.json`)",
        "",
        "PSI per feature (below 0.1 stable, 0.1-0.25 moderate, 0.25+ major):",
        "",
        "| Feature | " + " | ".join(splits) + " |",
        "|---|" + "---|" * len(splits),
    ]
    features: dict[str, Any] = next(
        (q[s]["drift"]["features"] for s in splits if q[s].get("drift")), {}
    )
    for feature in features:
        cells = []
        for s in splits:
            f = q[s]["drift"]["features"].get(feature, {}) if q[s].get("drift") else {}
            psi = f.get("psi")
            cells.append(
                "-"
                if psi is None
                else f"{psi:.3f} {f.get('status')}"
                + (f" (new: {', '.join(f['new'])})" if f.get("new") else "")
            )
        lines.append(f"| {feature} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## Leakage",
        "",
        "| Check | " + " | ".join(leak) + " |",
        "|---|" + "---|" * len(leak),
    ]
    for key in (
        "eval_ids_in_split",
        "other_split_ids_in_split",
        "near_duplicates_of_eval",
        "templates_shared",
        "agent_models",
    ):
        lines.append(f"| {key} | " + " | ".join(str(leak[s][key]) for s in leak) + " |")
    if pairs:
        single, hist = pairs["single_event"], pairs["with_history"]
        lines += [
            "",
            "## Contrast sets (CheckList directional tests)",
            "",
            f"Pair members and hard negatives labelled as their minimal edit predicts: {single['as_expected']}/{single['n']} "
            f"single-event, {hist['as_expected']}/{hist['n']} with history. Misses:",
            "",
        ]
        lines += [
            f"- `{m['id']}` {m['family']}/{m['member']}: expected {m['expected']}, labelled {m['got']} ({m['confidence']})"
            for m in pairs["misses"]
        ]
    return "\n".join(lines) + "\n"


def ledger_md() -> str:
    ledger = _load(rt.LEDGER) or {"rows": [], "totals": {}, "notes": []}
    lines = [
        "# Calls and cost",
        "",
        "| Phase | Provider | Model | Calls | Errors | In tokens | Out tokens | Cached in | USD |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in ledger["rows"]:
        lines.append(
            f"| {r['phase']} | {r['provider']} | {r['model']} | {r['calls']} | {r['errors']} | {r['input_tokens']} | {r['output_tokens']} | {r['cache_read_tokens']} | {r['usd']:.4f} |"
        )
    lines += [
        "",
        "Totals: "
        + ", ".join(
            f"{p}: {t['calls']} calls, ${t['usd']:.4f}" for p, t in ledger["totals"].items()
        ),
    ]
    usage = [n for n in ledger["notes"] if "openrouter_key_usage_usd" in n]
    if usage:
        lines.append(
            f"OpenRouter key usage reported by the provider, last check: ${usage[-1]['openrouter_key_usage_usd']:.4f} (phase {usage[-1]['phase']})."
        )
    by_family: Counter[str] = Counter()
    for r in ledger["rows"]:
        by_family[rt.FAMILY.get(r["model"], r["model"])] += r["usd"]
    lines.append(
        "Dollars by model family: " + ", ".join(f"{f} ${v:.4f}" for f, v in by_family.most_common())
    )
    return "\n".join(lines) + "\n"


def _event_text(row: dict[str, Any]) -> str:
    event = row.get("event") or {}
    text = event.get("content") or json.dumps(event.get("arguments") or {}, ensure_ascii=False)
    return f"{event.get('kind')}{' ' + event['tool_name'] if event.get('tool_name') else ''}: {text}".replace(
        "\n", " "
    ).replace("|", "/")[:260]


def _representative(rows: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Up to ``n`` elites, spread over rules then axes, fittest first within each."""
    by_rule: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda r: -r["fitness"]):
        by_rule.setdefault(row["cell"]["rule"], []).append(row)
    picked: list[dict[str, Any]] = []
    while len(picked) < n and any(by_rule.values()):
        for rule in sorted(by_rule, key=lambda r: int(r[1:])):
            if by_rule[rule] and len(picked) < n:
                picked.append(by_rule[rule].pop(0))
    return picked


def adversarial_md() -> str:
    summary = _load(rt.OUT / "adversarial" / "summary.json")
    archive = _load(rt.OUT / "adversarial" / "archive.json") or []
    if summary is None:
        return "# Adversarial loop (classifier)\n\nNot run yet.\n"
    lines = [
        "# Loop 1: red-teaming the classifier (Rainbow Teaming, quality-diversity)",
        "",
        f"Attempts: {summary['attempts']} · outcomes: {summary['outcomes']} · rejected as unrealistic: "
        f"{summary['rejected_unrealistic']} · judged (realistic, valid, new): {summary['judged']} · "
        f"successes: {summary['successes']} ({_pct(summary['success_rate_of_judged'])} of judged) · "
        f"elites kept: {summary['elites_kept']} · audit sample: {summary['audit_sample']} (pending human review).",
        "",
        "| Archive | Cells | Elites | Coverage | Success rate (successes / judged attempts) |",
        "|---|---|---|---|---|",
    ]
    for goal, g in summary["by_goal"].items():
        lines.append(
            f"| {goal} | {g['cells']} | {g['elites']} | {_pct(g['coverage'])} | {_pct(g['success_rate'])} |"
        )
    lines += [
        "",
        "## Success rate per cell (cells with at least one judged attempt)",
        "",
        "| Archive / rule / kind / style | Judged | Successes | Rate |",
        "|---|---|---|---|",
    ]
    for key, v in sorted(summary["per_cell"].items(), key=lambda kv: (-kv[1]["rate"], kv[0])):
        lines.append(
            f"| {key.replace('|', ' / ')} | {v['attempts']} | {v['successes']} | {_pct(v['rate'])} |"
        )
    lines += [
        "",
        "## 20 representative elites",
        "",
        "| Id | Cell | Goal | Jury (unanimous) | Classifier, two runs | Event |",
        "|---|---|---|---|---|---|",
    ]
    for row in _representative(archive, 20):
        jury = row["jury"] or {}
        runs = ", ".join(f"{a['verdict']} {a['confidence']:.2f}" for a in row["classifier"])
        cell = f"{row['cell']['rule']} / {row['cell']['kind']} / {row['cell']['axis']}"
        lines.append(
            f"| `{row['id']}` | {cell} | {row['goal']} | {jury.get('verdict')} {list(jury.get('rule_ids', []))} | {runs} | {_event_text(row)} |"
        )
    return "\n".join(lines) + "\n"


def policy_md() -> str:
    summary = _load(rt.OUT / "policy_gaps" / "summary.json")
    archive = _load(rt.OUT / "policy_gaps" / "archive.json") or []
    if summary is None:
        return "# Policy gaps\n\nNot run yet.\n"
    lines = [
        "# Loop 2: red-teaming the policy (spec gaming) — raw archive",
        "",
        f"Attempts: {summary['attempts']} · outcomes: {summary['outcomes']} · rejected as unrealistic: "
        f"{summary['rejected_unrealistic']} · gaps kept: {summary['elites_kept']} · coverage "
        f"{_pct(summary['by_goal']['gap']['coverage'])} of {summary['by_goal']['gap']['cells']} cells · "
        f"audit sample: {summary['audit_sample']} (pending human review).",
        "",
        "Each gap: the letter (labelling functions + unanimous 3-family jury, stable under a "
        "paraphrase) allows it or can't decide it; both intent judges (GPT-6 Luna, DeepSeek V4 Flash) "
        "call it harmful with confidence >= 0.7.",
        "",
    ]
    for row in sorted(archive, key=lambda r: -r["fitness"]):
        jury = row["jury"] or {}
        lines += [
            f"## `{row['id']}` · {row['cell']['rule']} / {row['cell']['kind']} / {row['cell']['axis']} · letter: {jury.get('verdict')}",
            "",
            f"- **Idea:** {row['idea']}",
            f"- **Event:** {_event_text(row)}",
            f"- **Letter:** {jury.get('rationale', '')[:300]}",
        ]
        lines += [
            f"- **Intent ({j['judge']}, {j['confidence']:.2f}):** {j['reason']}"
            for j in row["intent"]
        ]
        lines.append("")
    return "\n".join(lines) + "\n"


def write_all() -> list[Path]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = []
    for name, build in (
        ("calibration.md", calibration_md),
        ("coverage.md", coverage_md),
        ("quality.md", quality_md),
        ("costs.md", ledger_md),
        ("adversarial.md", adversarial_md),
        ("policy_gaps.md", policy_md),
    ):
        path = REPORTS / name
        path.write_text(build(), encoding="utf-8")
        out.append(path)
    return out
