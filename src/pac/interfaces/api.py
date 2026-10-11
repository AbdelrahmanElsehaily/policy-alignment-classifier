"""HTTP API for the demo app. Serves the built React app from ``web/dist`` when present."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import cached_property
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pac.bootstrap import (
    build_agent,
    build_baseline,
    build_classifier,
    build_policy,
    build_run_store,
    load_all_cases,
)
from pac.evaluation.application.ports import RunNotFound, RunStore
from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.domain.metrics import Metrics, Rate
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig
from pac.guardrail.application.ports import BackendError
from pac.guardrail.domain.classification import Classification, EventClassifier
from pac.guardrail.domain.events import ClassificationRequest
from pac.guardrail.domain.policy import Policy
from pac.guardrail.infrastructure.regex_classifier import RegexBaseline
from pac.interfaces.demo_examples import DemoExample, load_examples
from pac.interfaces.live_conversation import (
    SUGGESTIONS,
    ConversationSetup,
    CustomerView,
    TurnRequest,
)
from pac.settings import Settings, get_settings
from pac.simulation.application.agent import SimulatedAgent
from pac.simulation.application.conversation import GuardedConversation, executed_calls
from pac.simulation.domain.back_office import BackOffice

# Bump when a request or response shape the web app relies on changes. The app is read from disk
# on every page load but the server's code only at startup, so a server started before a change can
# end up serving a newer page; the page compares versions and asks for a restart.
API_VERSION = 3


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    api_version: int = API_VERSION
    backend: str
    model: str
    live: bool
    llm_available: bool
    llm_error: str | None


class PolicyView(BaseModel):
    version: str
    sha256: str
    rule_ids: list[str]
    text: str


class RunSummary(BaseModel):
    """One row of the results comparison: enough to compare runs without loading predictions."""

    run_id: str
    created_at: str
    classifier: str
    backend: str
    prompt_version: str
    split: str
    dataset: str
    n_cases: int
    repeats: int
    with_history: bool
    accuracy: Rate
    # For generated data: the accuracy interval when resampling whole templates.
    accuracy_template_ci: tuple[float, float] | None
    violation_recall: Rate
    false_block_rate: Rate
    silent_recall: Rate
    classifier_errors: Rate
    latency_p50_ms: float | None


class RunDetail(BaseModel):
    config: RunConfig
    metrics: Metrics
    predictions: list[Prediction]


class _Services:
    """Lazily built dependencies, so the app starts even when no LLM is configured."""

    def __init__(
        self,
        settings: Settings,
        classifier: EventClassifier | None,
        agent: SimulatedAgent | None,
    ) -> None:
        self.settings = settings
        self._classifier = classifier
        self._classifier_error: str | None = None
        self._agent = agent
        self._agent_error: str | None = None

    @cached_property
    def policy(self) -> Policy:
        return build_policy(self.settings)

    @cached_property
    def cases(self) -> list[LabeledCase]:
        return load_all_cases(self.settings)

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

    def agent(self) -> SimulatedAgent | None:
        if self._agent is None and self._agent_error is None:
            try:
                self._agent = build_agent(self.settings)
            except BackendError as exc:
                self._agent_error = str(exc)
        return self._agent

    def conversation(self) -> tuple[GuardedConversation | None, str | None]:
        """The agent with the LLM guardrail inline and the baseline alongside, or why not."""
        agent, classifier = self.agent(), self.classifier()
        if agent is None:
            return None, self._agent_error
        if classifier is None:
            return None, self._classifier_error
        return GuardedConversation(agent, classifier, [("baseline", self.baseline)]), None


def create_app(
    settings: Settings | None = None,
    classifier: EventClassifier | None = None,
    agent: SimulatedAgent | None = None,
) -> FastAPI:
    services = _Services(settings or get_settings(), classifier, agent)
    app = FastAPI(title="Policy alignment classifier", version="0.1.0")

    @app.get("/api/health")
    def health() -> Health:
        error = services.classifier_error
        return Health(
            backend=services.settings.backend,
            model=services.settings.model,
            live=services.settings.live,
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

    @app.get("/api/examples")
    def examples() -> list[DemoExample]:
        return load_examples(services.settings.examples_path, services.cases)

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

    @app.get("/api/conversation")
    def conversation_setup() -> ConversationSetup:
        conversation, error = services.conversation()
        agent = services.agent()
        return ConversationSetup(
            available=conversation is not None,
            error=error,
            agent_model=agent.model if agent is not None else None,
            customers=[
                CustomerView(customer_id=c.customer_id, name=c.name, email=c.email)
                for c in BackOffice.seeded().customers()
            ],
            suggestions=SUGGESTIONS,
        )

    @app.post("/api/conversation/turn")
    def conversation_turn(turn: TurnRequest) -> StreamingResponse:
        """Stream one turn as NDJSON: each event, its verdicts, and what the guardrail did."""
        conversation, error = services.conversation()
        if conversation is None:
            raise HTTPException(status_code=503, detail=error)
        office = BackOffice.seeded()
        office.replay(executed_calls(turn.history))

        async def lines() -> AsyncIterator[str]:
            async for update in conversation.turn(
                office,
                turn.session,
                turn.history,
                turn.message,
                guardrail_sees_history=turn.guardrail_sees_history,
            ):
                yield update.model_dump_json() + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

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
                    dataset=config.dataset,
                    n_cases=config.n_cases,
                    repeats=config.repeats,
                    with_history=config.with_history,
                    accuracy=metrics.accuracy,
                    accuracy_template_ci=metrics.accuracy_template_ci,
                    violation_recall=metrics.violation_recall,
                    false_block_rate=metrics.false_block_rate,
                    silent_recall=metrics.silent_recall,
                    classifier_errors=metrics.operational.classifier_errors,
                    latency_p50_ms=metrics.operational.model_latency_p50_ms,
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
