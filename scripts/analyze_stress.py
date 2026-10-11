"""Break stress-split runs down by family, by source, and by how much context precedes a turn.

    uv run python scripts/analyze_stress.py RUN_ID [RUN_ID ...]

Prints markdown tables. Accuracy alone misleads on this split (recorded traffic is mostly benign),
so every table shows violation recall and false blocks next to it.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence

from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.infrastructure.run_store import FileRunStore
from pac.settings import REPO_ROOT


def tag(p: Prediction, prefix: str) -> str | None:
    return next((t.removeprefix(prefix) for t in p.tags if t.startswith(prefix)), None)


def cell(hits: int, n: int) -> str:
    if n == 0:
        return "–"
    return f"{hits / n:.0%} ({hits}/{n})"


def scores(preds: Sequence[Prediction]) -> tuple[str, str, str, str]:
    violations = [p for p in preds if p.gold == "violation"]
    allows = [p for p in preds if p.gold == "allow"]
    silent = [p for p in preds if p.gold == "policy_silent"]
    return (
        cell(sum(p.correct for p in preds), len(preds)),
        cell(sum(p.predicted == "violation" for p in violations), len(violations)),
        cell(sum(p.predicted == "violation" for p in allows), len(allows)),
        cell(sum(p.outcome == "policy_silent" for p in silent), len(silent)),
    )


def table(
    title: str,
    runs: dict[str, list[Prediction]],
    key: Callable[[Prediction], str | None],
    order: Iterable[str] | None = None,
) -> None:
    print(f"\n### {title}\n")
    print("| group | run | n | accuracy | violations caught | false blocks | undecided flagged |")
    print("|---|---|---|---|---|---|---|")
    groups: dict[str, dict[str, list[Prediction]]] = defaultdict(lambda: defaultdict(list))
    for name, preds in runs.items():
        for p in preds:
            if (k := key(p)) is not None:
                groups[k][name].append(p)
    for group in order or sorted(groups):
        for name in runs:
            preds = groups[group][name]
            if preds:
                acc, rec, fb, sil = scores(preds)
                print(f"| {group} | {name} | {len(preds)} | {acc} | {rec} | {fb} | {sil} |")


def main(run_ids: list[str]) -> None:
    store = FileRunStore(REPO_ROOT / "results" / "runs")
    runs: dict[str, list[Prediction]] = {}
    for run_id in run_ids:
        saved = store.load(run_id)
        label = f"{saved.config.classifier}{' +history' if saved.config.with_history else ''}"
        runs[label] = [p for p in saved.predictions if p.repeat == 0]
        ci = saved.metrics.accuracy_template_ci
        print(
            f"- `{run_id}`: accuracy {saved.metrics.accuracy.value:.0%}"
            + (f", template interval [{ci[0]:.0%}–{ci[1]:.0%}]" if ci else "")
        )

    def source(p: Prediction) -> str:
        if "conversation" not in p.tags:
            return "single-event" if tag(p, "family:") != "window" else "window"
        return "recorded" if tag(p, "family:") == "recorded" else "scripted"

    table("By source", runs, source, ["recorded", "scripted", "window", "single-event"])
    table("By family", runs, lambda p: tag(p, "family:"))
    ctx_order = ["00-02", "03-05", "06-09", "10-14", "15+"]
    table(
        "Conversation turns by context length (events before the turn)",
        runs,
        lambda p: tag(p, "ctx:") if "conversation" in p.tags else None,
        ctx_order,
    )
    table("By event kind", runs, lambda p: p.kind)


if __name__ == "__main__":
    main(sys.argv[1:])
