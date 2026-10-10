"""HTTP API for the demo app. Serves the built React app from ``web/dist`` when present."""

from __future__ import annotations

from functools import cached_property
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pac.bootstrap import build_baseline, build_classifier, build_policy, build_run_store
from pac.evaluation.application.ports import RunNotFound, RunStore
from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.domain.metrics import Metrics
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig
from pac.evaluation.infrastructure.case_store import load_cases
from pac.guardrail.application.ports import BackendError
from pac.guardrail.domain.classification import Classification, EventClassifier
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.policy import Policy
from pac.guardrail.infrastructure.regex_classifier import RegexBaseline
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
    prompt_version: str
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

    @cached_property
    def policy(self) -> Policy:
        return build_policy(self.settings)

    @cached_property
    def cases(self) -> list[LabeledCase]:
        return load_cases(self.settings.cases_path)

    @cached_property
    def baseline(self) -> RegexBaseline:
        return build_baseline(self.settings)

    @cached_property
    def runs(self) -> RunStore:
        return build_run_store(self.settings)

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
            rule_ids=p.ordered_rule_ids(),
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
        for run_id in services.runs.list_ids():
            saved = services.runs.load(run_id)
            config, metrics = saved.config, saved.metrics
            summaries.append(
                RunSummary(
                    run_id=config.run_id,
                    created_at=config.created_at,
                    classifier=config.classifier,
                    backend=config.backend,
                    prompt_version=config.prompt_version,
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
        try:
            saved = services.runs.load(run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RunDetail(
            config=saved.config, metrics=saved.metrics, predictions=list(saved.predictions)
        )

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
