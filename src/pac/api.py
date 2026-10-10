"""HTTP API for the demo app. Serves the built React app from ``web/dist`` when present."""

from __future__ import annotations

from functools import cached_property
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pac.backends import BackendError
from pac.baseline import RegexBaseline
from pac.classifier import EventClassifier, build_classifier
from pac.eval.dataset import load_cases
from pac.eval.harness import list_runs, load_run
from pac.eval.metrics import Metrics
from pac.eval.records import Prediction, RunConfig
from pac.policy import Policy
from pac.schemas import Classification, ClassificationRequest, LabeledCase
from pac.settings import Settings, get_settings


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    backend: str
    model: str
    llm_available: bool
    llm_error: str | None


class PolicyView(BaseModel):
    version: str
    sha256: str
    rule_ids: list[str]
    text: str


class RunSummary(BaseModel):
    run_id: str
    created_at: str
    classifier: str
    backend: str
    split: str
    n_cases: int
    with_history: bool
    accuracy: float | None
    violation_recall: float | None
    false_block_rate: float | None


class RunDetail(BaseModel):
    config: RunConfig
    metrics: Metrics
    predictions: list[Prediction]


class _Services:
    """Lazily built dependencies, so the app starts even when no LLM is configured."""

    def __init__(self, settings: Settings, classifier: EventClassifier | None) -> None:
        self.settings = settings
        self._classifier = classifier
        self._classifier_error: str | None = None
        self.baseline = RegexBaseline()

    @cached_property
    def policy(self) -> Policy:
        return Policy.load(self.settings.policy_path)

    @cached_property
    def cases(self) -> list[LabeledCase]:
        return load_cases(self.settings.cases_path)

    def classifier(self) -> EventClassifier | None:
        if self._classifier is None and self._classifier_error is None:
            try:
                self._classifier = build_classifier(self.settings)
            except BackendError as exc:
                self._classifier_error = str(exc)
        return self._classifier

    @property
    def classifier_error(self) -> str | None:
        self.classifier()
        return self._classifier_error


def create_app(
    settings: Settings | None = None, classifier: EventClassifier | None = None
) -> FastAPI:
    services = _Services(settings or get_settings(), classifier)
    app = FastAPI(title="Policy alignment classifier", version="0.1.0")

    @app.get("/api/health")
    def health() -> Health:
        error = services.classifier_error
        return Health(
            backend=services.settings.backend,
            model=services.settings.model,
            llm_available=error is None,
            llm_error=error,
        )

    @app.get("/api/policy")
    def policy() -> PolicyView:
        p = services.policy
        return PolicyView(
            version=p.version,
            sha256=p.sha256,
            rule_ids=sorted(p.rule_ids, key=lambda r: int(r[1:])),
            text=p.text,
        )

    @app.get("/api/cases")
    def cases() -> list[LabeledCase]:
        return services.cases

    @app.post("/api/classify")
    async def classify(
        request: ClassificationRequest,
        engine: Annotated[Literal["llm", "baseline"], Query()] = "llm",
    ) -> Classification:
        if engine == "baseline":
            return await services.baseline.classify(request)
        classifier = services.classifier()
        if classifier is None:
            raise HTTPException(status_code=503, detail=services.classifier_error)
        return await classifier.classify(request)

    @app.get("/api/runs")
    def runs() -> list[RunSummary]:
        summaries = []
        for run_dir in list_runs(services.settings.results_dir):
            config, _, metrics = load_run(run_dir)
            summaries.append(
                RunSummary(
                    run_id=config.run_id,
                    created_at=config.created_at,
                    classifier=config.classifier,
                    backend=config.backend,
                    split=config.split,
                    n_cases=config.n_cases,
                    with_history=config.with_history,
                    accuracy=metrics.accuracy.value,
                    violation_recall=metrics.violation_recall.value,
                    false_block_rate=metrics.false_block_rate.value,
                )
            )
        return summaries

    @app.get("/api/runs/{run_id}")
    def run(run_id: str) -> RunDetail:
        run_dir = services.settings.results_dir / "runs" / run_id
        if run_id != run_dir.name or not (run_dir / "config.json").exists():
            raise HTTPException(status_code=404, detail=f"no run {run_id!r}")
        config, predictions, metrics = load_run(run_dir)
        return RunDetail(config=config, metrics=metrics, predictions=predictions)

    _mount_frontend(app, services.settings)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.web_dist
    index = dist / "index.html"
    if not index.exists():

        @app.get("/", include_in_schema=False)
        def no_frontend() -> dict[str, Any]:
            return {
                "message": "Frontend not built. Run `uv run pac serve` or `npm run build` in web/.",
                "api_docs": "/docs",
            }

        return

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def spa() -> FileResponse:
        return FileResponse(index)
