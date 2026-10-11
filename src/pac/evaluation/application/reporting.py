"""Markdown rendering of a scored run, for humans reviewing it in the repo (pure; no I/O)."""

from __future__ import annotations

from pac.evaluation.domain.metrics import Rate, SliceScores
from pac.evaluation.domain.run import ScoredRun
from pac.guardrail.domain.decision import VERDICTS


def fmt_rate(r: Rate) -> str:
    if r.value is None or r.ci_low is None or r.ci_high is None:
        return "n/a (n=0)"
    return f"{r.value:.0%} [{r.ci_low:.0%}–{r.ci_high:.0%}] ({r.k}/{r.n})"


def _num(value: float | None, spec: str = ".2f", suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:{spec}}{suffix}"


def _slice_table(title: str, slices: dict[str, SliceScores]) -> list[str]:
    lines = [
        f"### {title}",
        "",
        "| slice | n | accuracy | violation recall | false-block rate |",
        "|---|---|---|---|---|",
    ]
    for name, s in slices.items():
        lines.append(
            f"| {name} | {s.n} | {fmt_rate(s.accuracy)} | {fmt_rate(s.violation_recall)} "
            f"| {fmt_rate(s.false_block_rate)} |"
        )
    return [*lines, ""]


def _cell(text: str, limit: int = 220) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_report(run: ScoredRun) -> str:
    config, m, predictions = run.config, run.metrics, run.predictions
    op = m.operational
    f1_ci = f" [{m.macro_f1_ci[0]:.2f}–{m.macro_f1_ci[1]:.2f}]" if m.macro_f1_ci else ""
    lines = [
        f"# Eval run `{config.run_id}`",
        "",
        f"- **Classifier:** `{config.classifier}` via `{config.backend}`",
        f"- **Prompt:** `{config.prompt_version}` · **Policy:** `{config.policy_version}` "
        f"(`{config.policy_sha256[:12]}`) · "
        f"**Dataset:** `{config.dataset}` `{config.dataset_sha256[:12]}`",
        f"- **Split:** `{config.split}` · **Cases:** {m.n_cases} · **Repeats:** {m.repeats} · "
        f"**History:** {'on' if config.with_history else 'off'} · "
        f"**Commit:** `{config.git_commit or 'unknown'}`",
        "",
        "Intervals are 95% Wilson intervals (bootstrap for macro-F1). Headline numbers use "
        "repeat 0 only. Classifier errors are never scored as correct; they count as "
        "escalations only in the two operational rates (caught-or-escalated, escalation on "
        "allow), because a fail-closed error is routed to a human.",
        "",
        "## Headline",
        "",
        "| metric | value |",
        "|---|---|",
        f"| 3-way accuracy | {fmt_rate(m.accuracy)} |",
        f"| macro-F1 | {_num(m.macro_f1)}{f1_ci} |",
        f"| violation recall (flagged as violation) | {fmt_rate(m.violation_recall)} |",
        f"| violation caught or escalated | {fmt_rate(m.violation_caught_or_escalated)} |",
        f"| violation precision | {fmt_rate(m.violation_precision)} |",
        f"| false-block rate (allow → violation) | {fmt_rate(m.false_block_rate)} |",
        f"| escalation rate on allow (allow → silent) | {fmt_rate(m.escalation_rate_on_allow)} |",
        f"| policy-silent recall | {fmt_rate(m.silent_recall)} |",
        f"| rule attribution (right rule, given flagged) | {fmt_rate(m.rule_attribution)} |",
        f"| classifier errors (fail-closed) | {fmt_rate(op.classifier_errors)} |",
    ]
    if m.flip_rate is not None:
        lines.append(f"| verdict flip rate across repeats | {fmt_rate(m.flip_rate)} |")

    lines += ["", "## Confusion matrix (rows: gold, columns: predicted)", ""]
    columns = (*VERDICTS, "error")
    lines += ["| gold \\ pred | " + " | ".join(columns) + " |", "|---|---|---|---|---|"]
    for gold in VERDICTS:
        row = " | ".join(str(m.confusion[gold].get(pred, 0)) for pred in columns)
        lines.append(f"| **{gold}** | {row} |")

    lines += ["", "## Breakdowns", ""]
    lines += _slice_table("By event kind", m.by_kind)
    lines += _slice_table("By label confidence", m.by_label_confidence)
    lines += _slice_table("By origin", m.by_origin)
    lines += _slice_table("By tag", m.by_tag)

    lines += [
        "## Calibration",
        "",
        "| confidence | n | mean confidence | accuracy |",
        "|---|---|---|---|",
    ]
    for b in m.calibration:
        lines.append(
            f"| {b.low:.1f}–{b.high:.1f} | {b.n} | {_num(b.mean_confidence)} | {_num(b.accuracy)} |"
        )
    lines += ["", f"Expected calibration error: {_num(m.expected_calibration_error, '.3f')}", ""]

    lines += [
        "## Cost and latency",
        "",
        f"- Model latency p50 / p95: {_num(op.model_latency_p50_ms, '.0f', ' ms')} / "
        f"{_num(op.model_latency_p95_ms, '.0f', ' ms')}",
        f"- Mean tokens per event: {op.mean_input_tokens:.0f} in "
        f"({op.mean_cache_read_tokens:.0f} cache-read) / {op.mean_output_tokens:.0f} out",
        "- Cost per 1,000 events: "
        + (
            f"${op.cost_per_1k_events_usd:.4f}"
            if op.cost_per_1k_events_usd is not None
            else "n/a (no list price for this model)"
        ),
        f"- Served from cache: {op.cached_fraction:.0%}",
        "",
    ]

    errors = [p for p in predictions if p.repeat == 0 and not p.correct]
    lines += [
        f"## Disagreements with the gold label ({len(errors)})",
        "",
        "| case | kind | gold | predicted | model rationale |",
        "|---|---|---|---|---|",
    ]
    for p in errors:
        d = p.result.decision
        shown = d.verdict + (f" ({', '.join(d.rule_ids)})" if d.rule_ids else "")
        gold_text = p.gold + (f" ({', '.join(p.gold_rules)})" if p.gold_rules else "")
        lines.append(f"| `{p.case_id}` | {p.kind} | {gold_text} | {shown} | {_cell(d.rationale)} |")
    return "\n".join(lines) + "\n"
