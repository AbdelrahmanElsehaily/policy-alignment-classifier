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
    compute_north_stars,
    dataset_path,
    expected_profile,
    load_all_cases,
    profile_of_run,
)
from pac.evaluation.application.ports import RunNotFound
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
from pac.monitoring.domain.drift import Profile, compare
from pac.monitoring.infrastructure.exporters import load_profile, save_profile
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
    stress = "stress"
    agents = "agents"
    agents_pressure = "agents-pressure"
    long_context = "long-context"
    train = "train"  # for prompt optimisation (scripts/optimize_prompt.py), never for reporting
    all = "all"  # dev + test (the later splits are run on their own)


BackendOpt = Annotated[
    str | None,
    typer.Option(
        help="anthropic | openai | openrouter | minimax | ollama | replay (default: PAC_BACKEND)"
    ),
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
        temperature=None if backend_name == "regex" else settings.temperature,
        max_event_chars=None if backend_name == "regex" else settings.max_event_chars,
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
    SplitOption.stress: "stress",
    SplitOption.agents: "agents",
    SplitOption.agents_pressure: "agents-pressure",
    SplitOption.long_context: "long-context",
    SplitOption.train: "train",
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
        f"{by_split['stress']} stress, {by_split['agents']} agents, "
        f"{by_split['agents-pressure']} agents-pressure, {by_split['train']} train)"
    )


monitor_app = typer.Typer(
    help="Drift monitoring: profile a run as the reference, compare other traffic with it.",
    no_args_is_help=True,
)
app.add_typer(monitor_app, name="monitor")

DRIFT_EXIT_CODE = 4


def _profile_of_run(settings: Settings, run_id: str) -> Profile:
    try:
        return profile_of_run(settings, run_id)
    except RunNotFound as exc:
        raise _fail(str(exc)) from exc


@monitor_app.command("reference")
def monitor_reference(
    run_id: Annotated[
        str | None,
        typer.Argument(help="Profile this run instead of the labelled expected traffic"),
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Where to write the profile")] = None,
) -> None:
    """Build the reference profile that live traffic is compared with.

    By default: the test split and the recorded conversations, with their gold labels, so the
    reference doesn't depend on any model. Given a run, it profiles that run's answers instead.
    """
    settings = Settings()
    if run_id is None:
        profile, source = expected_profile(settings), "the test split and recorded conversations"
    else:
        profile, source = _profile_of_run(settings, run_id), run_id
    path = out or settings.monitoring_reference_path
    save_profile(profile, path)
    typer.echo(f"Wrote {path}: {profile.events} events from {source}")


@monitor_app.command("compare")
def monitor_compare(
    run_id: Annotated[str, typer.Argument(help="The run to check against the reference")],
    reference: Annotated[Path | None, typer.Option(help="Reference profile")] = None,
    min_events: Annotated[
        int, typer.Option(min=1, help="Fewer events than this: no verdict")
    ] = 100,
) -> None:
    """Report how far a run's traffic has drifted from the reference. Exits 4 on major drift."""
    settings = Settings()
    ref = load_profile(reference or settings.monitoring_reference_path)
    if ref is None:
        raise _fail("No reference profile yet. Build one: uv run pac monitor reference")
    report = compare(ref, _profile_of_run(settings, run_id), min_events=min_events)
    typer.echo(
        f"Drift of {run_id} ({report.current_events} events) against the reference "
        f"({report.reference_events} events): {report.status.upper()}\n"
    )
    typer.echo(f"  {'feature':<12} {'group':<7} {'PSI':>6}  {'status':<12} biggest moves")
    for f in report.features:
        moved = ", ".join(f"{c} {r:.0%}→{n:.0%}" for c, r, n in f.movers)
        if f.new:
            moved = f"NEW: {', '.join(f.new)}; {moved}"
        value = "–" if f.psi is None else f"{f.psi:.3f}"
        typer.echo(f"  {f.feature:<12} {f.group:<7} {value:>6}  {f.status:<12} {moved}")
    typer.echo("\nPSI: below 0.1 stable, 0.1-0.25 moderate, 0.25 and above major.")
    if report.status == "major":
        raise typer.Exit(code=DRIFT_EXIT_CODE)


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


@app.command("north-star")
def north_star() -> None:
    """The north star: of every held-out event, the share each classifier handles as labelled."""
    stars = compute_north_stars(Settings())
    for s in stars:
        prompt = s.prompt_version.split("@")[0] if s.prompt_version != "n/a" else "-"
        spread = f"{s.low:.1%}–{s.high:.1%}" if s.high > s.low else ""
        missing = f"  (not run on: {', '.join(s.missing)})" if s.missing else ""
        score = f"{s.accuracy:6.1%}  {s.correct}/{s.total}"
        typer.echo(f"{s.classifier:30} {prompt:16} {score}  {spread}{missing}")
