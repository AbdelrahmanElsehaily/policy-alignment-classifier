"""Command-line entry point: ``uv run pac --help``."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, get_args

import typer
from pydantic import ValidationError

from pac.bootstrap import (
    build_backend,
    build_baseline,
    build_classifier,
    build_policy,
    dataset_path,
    load_all_cases,
)
from pac.evaluation.application.reporting import fmt_rate
from pac.evaluation.application.run_eval import new_run_id, record_run, rescore_run, run_cases
from pac.evaluation.domain.cases import (
    DatasetError,
    LabeledCase,
    Split,
    check_against_policy,
    select,
)
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig
from pac.evaluation.infrastructure.case_store import file_sha256, load_cases
from pac.evaluation.infrastructure.provenance import git_commit
from pac.evaluation.infrastructure.run_store import FileRunStore
from pac.guardrail.application.ports import BackendError
from pac.guardrail.domain.classification import EventClassifier
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.policy import Policy
from pac.settings import BackendName, Settings
from pac.telemetry import configure_logging, log_context

app = typer.Typer(
    help="Policy alignment classifier for AI agent trace events.",
    no_args_is_help=True,
    add_completion=False,
)


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormatOption(StrEnum):
    text = "text"
    json = "json"


@app.callback()
def main(
    log_level: Annotated[
        LogLevel | None,
        typer.Option(help="DEBUG shows every classification (default: PAC_LOG_LEVEL or INFO)"),
    ] = None,
    log_format: Annotated[
        LogFormatOption | None,
        typer.Option(help="text, or one JSON object per line (default: PAC_LOG_FORMAT or text)"),
    ] = None,
) -> None:
    """Logs go to stderr; results and progress for people go to stdout."""
    settings = Settings()
    configure_logging(
        log_level.value if log_level else settings.log_level,
        log_format.value if log_format else settings.log_format,
    )


class ClassifierKind(StrEnum):
    llm = "llm"
    baseline = "baseline"


class SplitOption(StrEnum):
    dev = "dev"
    test = "test"
    hard = "hard"
    stress = "stress"
    all = "all"  # dev + test (hard and stress are run on their own)


BackendOpt = Annotated[
    str | None, typer.Option(help="anthropic | minimax | ollama | replay (default: PAC_BACKEND)")
]
ModelOpt = Annotated[str | None, typer.Option(help="Model id (default: PAC_MODEL)")]


def _settings(backend: str | None, model: str | None, **extra: Any) -> Settings:
    overrides: dict[str, Any] = {k: v for k, v in extra.items() if v is not None}
    if backend is not None:
        if backend not in get_args(BackendName):
            raise typer.BadParameter(f"unknown backend {backend!r}", param_hint="--backend")
        overrides["backend"] = backend
    if model is not None:
        overrides["model"] = model
    return Settings(**overrides)


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(code=1)


@app.command()
def classify(
    request_file: Annotated[
        Path | None,
        typer.Argument(
            help="JSON ClassificationRequest; reads stdin if omitted", exists=True, dir_okay=False
        ),
    ] = None,
    backend: BackendOpt = None,
    model: ModelOpt = None,
) -> None:
    """Classify one event and print the result as JSON. Exits 1 if the classifier failed closed."""
    raw = request_file.read_text() if request_file else sys.stdin.read()
    try:
        request = ClassificationRequest.model_validate_json(raw)
    except ValidationError as exc:
        raise _fail(f"invalid request: {exc}") from exc
    try:
        classifier = build_classifier(_settings(backend, model))
    except BackendError as exc:
        raise _fail(str(exc)) from exc
    result = asyncio.run(classifier.classify(request))
    typer.echo(result.model_dump_json(indent=2))
    if result.error is not None:
        raise _fail(f"classifier failed closed: {result.error}")


@app.command(name="eval")
def evaluate(
    split: Annotated[SplitOption, typer.Option(help="Which split to run")] = SplitOption.dev,
    classifier_kind: Annotated[
        ClassifierKind, typer.Option("--classifier", help="LLM classifier or regex baseline")
    ] = ClassifierKind.llm,
    backend: BackendOpt = None,
    model: ModelOpt = None,
    repeats: Annotated[int, typer.Option(min=1, help="Runs per case (needs --no-cache)")] = 1,
    with_history: Annotated[
        bool, typer.Option(help="Ablation: give the classifier earlier events too")
    ] = False,
    cache: Annotated[bool, typer.Option(help="Read/write the response cache")] = True,
    concurrency: Annotated[int | None, typer.Option(min=1)] = None,
    case_id: Annotated[
        list[str] | None, typer.Option("--case", help="Only run these case ids (repeatable)")
    ] = None,
    out_dir: Annotated[
        Path | None, typer.Option(help="Where to write the run (default: results/runs/<id>)")
    ] = None,
    max_error_rate: Annotated[
        float,
        typer.Option(min=0.0, max=1.0, help="Exit 3 if more cases than this fail closed"),
    ] = 0.1,
) -> None:
    """Run a classifier over the eval set and write a scored run."""
    if repeats > 1 and cache:
        raise typer.BadParameter(
            "repeats measure run-to-run variance; with the cache on they would be identical. "
            "Add --no-cache.",
            param_hint="--repeats",
        )
    settings = _settings(backend, model, concurrency=concurrency)
    policy = build_policy(settings)
    dataset = dataset_path(settings, _SPLITS.get(split))
    cases = _select_cases(dataset, policy, split, case_id)
    classifier, prompt_version, backend_name = _make_classifier(
        settings, classifier_kind, cache=cache
    )

    run_id = new_run_id(classifier.name, split.value, history=with_history)
    run_dir = out_dir or settings.results_dir / "runs" / run_id
    typer.echo(f"Running {classifier.name} on {len(cases)} {split.value} case(s)…", err=True)
    with log_context(run=run_dir.name):
        predictions = asyncio.run(
            run_cases(
                classifier,
                cases,
                repeats=repeats,
                concurrency=settings.concurrency,
                with_history=with_history,
                on_result=_progress_printer(len(cases) * repeats),
            )
        )
    config = RunConfig(
        run_id=run_dir.name,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        classifier=classifier.name,
        backend=backend_name,
        prompt_version=prompt_version,
        policy_version=policy.version,
        policy_sha256=policy.sha256,
        dataset_sha256=file_sha256(dataset),
        dataset=dataset.name,
        split=split.value,
        n_cases=len(cases),
        repeats=repeats,
        with_history=with_history,
        git_commit=git_commit(),
    )
    with log_context(run=run_dir.name):
        metrics = record_run(FileRunStore(run_dir.parent), config, predictions).metrics

    typer.echo(f"\nWrote {run_dir}")
    typer.echo(f"  accuracy           {fmt_rate(metrics.accuracy)}")
    typer.echo(f"  violation recall   {fmt_rate(metrics.violation_recall)}")
    typer.echo(f"  false-block rate   {fmt_rate(metrics.false_block_rate)}")
    typer.echo(f"  silent recall      {fmt_rate(metrics.silent_recall)}")
    typer.echo(f"  classifier errors  {fmt_rate(metrics.operational.classifier_errors)}")
    errors = metrics.operational.classifier_errors
    if errors.value is not None and errors.value > max_error_rate:
        typer.secho(
            f"WARNING: {errors.value:.0%} of cases failed closed (limit {max_error_rate:.0%}); "
            "this run measures the failure mode, not the classifier. Check the backend.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=3)


_SPLITS: dict[SplitOption, Split] = {
    SplitOption.dev: "dev",
    SplitOption.test: "test",
    SplitOption.hard: "hard",
    SplitOption.stress: "stress",
}


def _select_cases(
    dataset: Path, policy: Policy, split: SplitOption, case_ids: list[str] | None
) -> list[LabeledCase]:
    try:
        all_cases = load_cases(dataset)
        check_against_policy(all_cases, policy)
    except DatasetError as exc:
        raise _fail(f"dataset invalid: {exc}") from exc

    cases = select(all_cases, _SPLITS.get(split))
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]
        missing = wanted - {c.id for c in cases}
        if missing:
            raise _fail(f"unknown case ids for split {split.value}: {sorted(missing)}")
    if not cases:
        raise _fail("no cases selected")
    return cases


def _make_classifier(
    settings: Settings, kind: ClassifierKind, *, cache: bool
) -> tuple[EventClassifier, str, str]:
    """Return the classifier plus the prompt version and backend name to record."""
    if kind is ClassifierKind.baseline:
        return build_baseline(settings), "n/a", "regex"
    if not cache and settings.backend == "replay":
        raise typer.BadParameter(
            "--no-cache with --backend replay would replay the cache anyway",
            param_hint="--no-cache",
        )
    try:
        backend = build_backend(settings, use_cache=cache)
        llm = build_classifier(settings, backend=backend)
    except BackendError as exc:
        raise _fail(str(exc)) from exc
    return llm, llm.prompt.version, settings.backend


def _progress_printer(total: int) -> Callable[[Prediction], None]:
    """Print one mark per result: '.' agrees with gold, 'x' disagrees, 'E' classifier error."""
    done = 0

    def progress(p: Prediction) -> None:
        nonlocal done
        done += 1
        mark = "." if p.correct else ("E" if p.result.error else "x")
        typer.echo(mark, nl=done % 50 == 0 or done == total, err=True)

    return progress


@app.command()
def report(run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)]) -> None:
    """Recompute metrics.json and report.md from a run's saved predictions."""
    rescore_run(FileRunStore(run_dir.parent), run_dir.name)
    typer.echo(f"Rewrote {run_dir / 'metrics.json'} and report.md")


@app.command(name="check-data")
def check_data() -> None:
    """Validate the eval set against the schema and the policy."""
    settings = Settings()
    try:
        cases = load_all_cases(settings)
        check_against_policy(cases, build_policy(settings))
    except DatasetError as exc:
        raise _fail(str(exc)) from exc
    by_split = Counter(c.split for c in cases)
    typer.echo(
        f"OK: {len(cases)} cases ({by_split['dev']} dev, {by_split['test']} test, "
        f"{by_split['hard']} hard, {by_split['stress']} stress)"
    )


@app.command()
def serve(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    rebuild: Annotated[bool, typer.Option(help="Rebuild the frontend even if built")] = False,
    live: Annotated[
        bool,
        typer.Option(help="Call the model on every request (no cache); needs a real backend"),
    ] = False,
) -> None:
    """Build the React app if needed, then serve the API and UI on one port."""
    import uvicorn  # noqa: PLC0415 - server-only dependency, keeps CLI startup fast

    from pac.interfaces.api import create_app  # noqa: PLC0415

    settings = Settings(live=True) if live else Settings()
    if settings.live and settings.backend == "replay":
        raise _fail(
            "--live needs a real model. For example:\n"
            "  PAC_BACKEND=ollama PAC_MODEL=qwen3.5 uv run pac serve --live\n"
            "  PAC_BACKEND=minimax PAC_MODEL=MiniMax-M3 MINIMAX_API_KEY=... uv run pac serve --live"
        )
    if rebuild or _frontend_stale(settings.web_dist):
        _build_frontend(settings.web_dist.parent)
    mode = "LIVE: every request calls the model" if settings.live else "recorded answers"
    typer.echo(
        f"Serving on http://{host}:{port}  "
        f"(backend={settings.backend}, model={settings.model}, {mode})"
    )
    # uvicorn logs through pac's handler (configured in main); requests are logged, with their
    # request id, by the API's own middleware.
    uvicorn.run(create_app(settings), host=host, port=port, log_config=None, access_log=False)


def _frontend_stale(dist: Path) -> bool:
    """True when the build is missing or older than any frontend source (e.g. after a pull)."""
    index = dist / "index.html"
    if not index.exists():
        return True
    web = dist.parent
    sources = [*(web / "src").rglob("*"), web / "index.html", web / "package-lock.json"]
    built = index.stat().st_mtime
    return any(p.is_file() and p.stat().st_mtime > built for p in sources)


def _build_frontend(web_dir: Path) -> None:
    npm = shutil.which("npm")
    if npm is None:
        typer.secho(
            "npm not found: serving the API only (UI needs Node.js 22.12+).",
            fg=typer.colors.YELLOW,
        )
        return
    typer.echo("Building the frontend (first run only)…")
    for args in (["ci", "--no-audit", "--no-fund"], ["run", "build"]):
        result = subprocess.run([npm, *args], cwd=web_dir, check=False)
        if result.returncode != 0:
            raise _fail(f"`npm {' '.join(args)}` failed in {web_dir}")
