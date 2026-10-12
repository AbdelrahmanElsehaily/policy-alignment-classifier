"""HTTP API for the demo app. Serves the built React app from ``web/dist`` when present."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import cached_property
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pac.bootstrap import (
    build_agent,
    build_baseline,
    build_classifier,
    build_monitor,
    build_policy,
    build_prompt,
    build_run_store,
    load_all_cases,
    profile_of_run,
)
from pac.evaluation.application.ports import RunNotFound, RunStore
from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.domain.metrics import Metrics, Rate
from pac.evaluation.domain.prediction import Prediction
from pac.evaluation.domain.run import RunConfig
from pac.guardrail.application.llm_classifier import LLMClassifier
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
from pac.monitoring.application.monitor import Monitor, MonitoredClassifier, MonitoringSnapshot
from pac.monitoring.domain.drift import DriftReport, compare
from pac.monitoring.infrastructure.exporters import load_profile, prometheus_text
from pac.settings import Settings, get_settings
from pac.simulation.application.agent import SimulatedAgent
from pac.simulation.application.conversation import GuardedConversation, executed_calls
from pac.simulation.domain.back_office import BackOffice
from pac.telemetry import current_context, log_context, new_id

logger = logging.getLogger(__name__)

# Bump when a request or response shape the web app relies on changes. The app is read from disk
# on every page load but the server's code only at startup, so a server started before a change can
# end up serving a newer page; the page compares versions and asks for a restart.
API_VERSION = 5


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
    # The policy the classifier judges by differs from the policy file (edited in the app).
    edited: bool = False
    # Why it can't be edited here (replay mode, no model), or None.
    locked: str | None = None


class PolicyEdit(BaseModel):
    text: str = Field(min_length=1, max_length=100_000)


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
        self._policy_override: Policy | None = None

    @cached_property
    def file_policy(self) -> Policy:
        return build_policy(self.settings)

    @property
    def policy(self) -> Policy:
        """The policy the classifier judges by: the file's, unless edited in the app."""
        return self._policy_override or self.file_policy

    def policy_locked(self) -> str | None:
        """Why the policy can't be edited right now, or None."""
        if self.settings.backend == "replay":
            return (
                "Replay mode only has recorded answers for the policy file, so an edited policy "
                "would get none. Start the server with a model to edit it, for example "
                "PAC_BACKEND=minimax PAC_MODEL=MiniMax-M3 uv run pac serve --live."
            )
        if self.classifier_error is not None:
            return f"No classifier to apply it to: {self.classifier_error}"
        classifier = self._classifier
        if isinstance(classifier, MonitoredClassifier):
            classifier = classifier.inner
        if not isinstance(classifier, LLMClassifier):
            return "This classifier doesn't take a policy."
        return None

    def set_policy(self, policy: Policy | None) -> None:
        """Judge by ``policy`` from now on (None: the file's), with the same backend and monitor.

        The agent's instructions don't change: the guardrail's policy is never shown to it.
        """
        classifier = self.classifier()
        if isinstance(classifier, MonitoredClassifier):
            classifier = classifier.inner
        if not isinstance(classifier, LLMClassifier):
            raise ValueError("this classifier doesn't take a policy")
        target = policy or self.file_policy
        self._classifier = classifier.with_policy(target, build_prompt(self.settings, target))
        self._policy_override = policy

    @cached_property
    def cases(self) -> list[LabeledCase]:
        return load_all_cases(self.settings)

    @cached_property
    def baseline(self) -> RegexBaseline:
        return build_baseline(self.settings)

    @cached_property
    def runs(self) -> RunStore:
        return build_run_store(self.settings)

    @cached_property
    def monitor(self) -> Monitor:
        return build_monitor(self.settings)

    def classifier(self) -> EventClassifier | None:
        """The LLM classifier, wrapped so every verdict it gives is measured for drift."""
        if self._classifier is None and self._classifier_error is None:
            try:
                self._classifier = build_classifier(self.settings)
            except BackendError as exc:
                self._classifier_error = str(exc)
        if self._classifier is not None and not isinstance(self._classifier, MonitoredClassifier):
            self._classifier = MonitoredClassifier(self._classifier, self.monitor)
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

    _trace_requests(app)

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

    _policy_routes(app, services)

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

        ids = {**current_context(), "turn": new_id()}

        async def lines() -> AsyncIterator[str]:
            # The body streams after this handler returns, so re-enter the request's ids here.
            with log_context(**ids):
                async for line in _stream():
                    yield line

        async def _stream() -> AsyncIterator[str]:
            async for update in conversation.turn(
                office,
                turn.session,
                turn.history,
                turn.message,
                guardrail_sees_history=turn.guardrail_sees_history,
            ):
                yield update.model_dump_json() + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    _monitoring_routes(app, services)

    @app.get("/api/runs")
    def runs() -> list[RunSummary]:
        summaries = []
        for run_id in services.runs.list_ids():
            try:
                saved = services.runs.load(run_id)
            except Exception:  # one bad run file mustn't hide all the others
                logger.warning("skipping unreadable run", extra={"run_id": run_id}, exc_info=True)
                continue
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


def _policy_routes(app: FastAPI, services: _Services) -> None:
    """The policy the classifier judges by: read it, edit it (with a model), reset it."""

    def policy_view() -> PolicyView:
        p = services.policy
        return PolicyView(
            version=p.version,
            sha256=p.sha256,
            rule_ids=p.ordered_rule_ids(),
            text=p.text,
            edited=p.sha256 != services.file_policy.sha256,
            locked=services.policy_locked(),
        )

    @app.get("/api/policy")
    def policy() -> PolicyView:
        return policy_view()

    @app.put("/api/policy")
    def edit_policy(edit: PolicyEdit) -> PolicyView:
        """Judge by an edited policy until reset or restart; the policy file is not changed."""
        locked = services.policy_locked()
        if locked is not None:
            raise HTTPException(status_code=409, detail=locked)
        try:
            edited = Policy.from_text(edit.text)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        same = edited.sha256 == services.file_policy.sha256
        services.set_policy(None if same else edited)
        logger.info(
            "policy changed",
            extra={"sha256": edited.sha256[:12], "rules": len(edited.rule_ids), "file": same},
        )
        return policy_view()

    @app.delete("/api/policy")
    def reset_policy() -> PolicyView:
        if services.policy is not services.file_policy:
            services.set_policy(None)
            logger.info("policy reset to the file")
        return policy_view()


def _monitoring_routes(app: FastAPI, services: _Services) -> None:
    """Live drift of judged traffic, a recorded run's drift, and Prometheus metrics."""

    @app.get("/api/monitoring")
    def monitoring() -> MonitoringSnapshot:
        """Live counts and drift of the traffic this server has judged, against the reference."""
        return services.monitor.snapshot()

    @app.get("/api/monitoring/runs/{run_id}")
    def run_drift(run_id: str) -> DriftReport:
        """How far a recorded run's traffic is from the reference profile."""
        reference = load_profile(services.settings.monitoring_reference_path)
        if reference is None:
            raise HTTPException(
                status_code=404,
                detail="No reference profile yet. Build one: uv run pac monitor reference",
            )
        try:
            current = profile_of_run(services.settings, run_id)
        except RunNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return compare(reference, current, min_events=services.settings.monitoring_min_events)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> PlainTextResponse:
        """The same, in Prometheus' text format, for scraping."""
        return PlainTextResponse(
            prometheus_text(services.monitor.snapshot()),
            media_type="text/plain; version=0.0.4",
        )


def _trace_requests(app: FastAPI) -> None:
    """Give every API request an id (or keep the caller's X-Request-ID), log it, return it.

    The id is in the log context while the request is handled, so every line it causes, down to
    the classifier, carries ``request=<id>``.
    """

    @app.middleware("http")
    async def trace_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or new_id()
        started = time.perf_counter()
        with log_context(request=request_id):
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("request failed", extra={"path": request.url.path})
                raise
            if request.url.path.startswith("/api/"):
                logger.info(
                    "%s %s %s",
                    request.method,
                    request.url.path,
                    response.status_code,
                    extra={"ms": round((time.perf_counter() - started) * 1000)},
                )
        response.headers["X-Request-ID"] = request_id
        return response


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
