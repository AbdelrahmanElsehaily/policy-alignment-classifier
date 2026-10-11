"""Optimise the classifier prompt with DSPy on the train split, then export it.

    uv sync --group optimize
    uv run --group optimize python scripts/optimize_prompt.py --dry-run     # sizes, no calls
    uv run --group optimize python scripts/optimize_prompt.py --optimizer miprov2
    uv run --group optimize python scripts/optimize_prompt.py --optimizer gepa

What is optimised. The middle of prompts/classifier_v2.md (the "How to judge" and "Untrusted
content" instructions). MIPROv2 also chooses up to four worked examples; GEPA optimises the
instructions only. The head (role, ``$policy``) and the tail (output contract, ``$schema``)
stay fixed, so the policy, the schema and the parser are untouched. Nothing is fine-tuned: the
model's weights never change.

On what. ``data/train_cases.jsonl`` only, single-event setting. A quarter of its templates
(whole templates, so rephrasings of one idea never straddle the split) are held out as the
validation set both optimisers select on. Dev, test, hard and stress are never read here.

How the prompt reaches the model. A custom DSPy adapter renders each candidate exactly as the
harness renders the exported file: the system prompt through ``PromptTemplate.from_text`` and
the user turn through ``prompt_optimization.user_message`` (tested byte-identical to
``PromptTemplate.render``), parsed with ``parse_decision``, one repair retry, then fail closed
to ``policy_silent``, like ``LLMClassifier``. What DSPy scores is what ``pac eval`` will run.
Every candidate's instructions pass through ``prepare_instructions`` before they are scored:
output-format and example sections are dropped, and so is any line that quotes a train case's
order, customer or card number, email or amount (those lines are counted in the report).

Model and budget. MiniMax-M3 only, through its OpenAI-compatible API via LiteLLM, thinking
disabled. DSPy's response cache (``results/scratch/dspy_cache``) makes reruns free. Every
request LiteLLM actually sends, retries included, is counted in a ledger
(``results/optimization/<optimizer>/minimax_calls.json``) that persists across runs; the
script stops before ``--budget`` is exceeded and writes nothing if it had to stop. HTTP 429s
are retried by LiteLLM with backoff, then waited out here.
"""

from __future__ import annotations

import argparse
import contextlib
import contextvars
import importlib.util
import json
import logging
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import dspy
import litellm
import prompt_optimization as po
from dspy.utils.exceptions import AdapterParseError
from litellm.integrations.custom_logger import CustomLogger
from pydantic import ValidationError

from pac.evaluation.domain.cases import LabeledCase
from pac.evaluation.infrastructure.case_store import load_cases
from pac.guardrail.application.prompting import REPAIR_INSTRUCTION, PromptTemplate, parse_decision
from pac.guardrail.domain.events import TraceEvent
from pac.guardrail.domain.policy import Policy
from pac.guardrail.infrastructure.files import load_policy
from pac.settings import REPO_ROOT, Settings

logger = logging.getLogger("optimize_prompt")

MODEL = "MiniMax-M3"
EXTRA_BODY = {"thinking": {"type": "disabled"}, "reasoning_split": True}
CACHE_DIR = REPO_ROOT / "results" / "scratch" / "dspy_cache"
RATE_LIMIT_WAIT_S = 60.0
RATE_LIMIT_WAITS = 10
OPTIMIZERS = ("miprov2", "gepa")
# The file suffix and the ledger budget each optimiser gets by default.
OUTPUT_NAME = {"miprov2": "mipro", "gepa": "gepa"}
DEFAULT_BUDGET = {"miprov2": 1100, "gepa": 800}
# Long outputs: a full instruction set doesn't fit in the classifier's 1,024 tokens.
PROPOSER_MAX_TOKENS = 4096

# =============================================================================================
# Call ledger and budget
# =============================================================================================


class BudgetExhausted(RuntimeError):
    """Raised before a call that would take the job past its MiniMax budget."""


class CallLedger(CustomLogger):  # type: ignore[misc]
    """Counts every request LiteLLM sends to the provider, in a file that outlives the run.

    Cache hits are answered by DSPy and never reach LiteLLM, so they are not counted; every
    attempt that does reach the provider is, including retries after a 429.
    """

    def __init__(self, path: Path, budget: int) -> None:
        super().__init__()
        self._path = path
        self._lock = threading.Lock()
        self.budget = budget
        state = json.loads(path.read_text()) if path.exists() else {}
        self.sent: int = state.get("sent", 0)
        self.failed: int = state.get("failed", 0)
        self.by_phase: dict[str, int] = state.get("by_phase", {})
        self.phase = "setup"
        # Set once the budget has stopped a call: the search's scores are then meaningless.
        self.exhausted = False
        self._save()  # the ledger exists from the start, even before the first call

    def log_pre_api_call(self, model: Any, messages: Any, kwargs: Any) -> None:
        with self._lock:
            self.sent += 1
            self.by_phase[self.phase] = self.by_phase.get(self.phase, 0) + 1
            self._save()

    def log_failure_event(
        self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any
    ) -> None:
        with self._lock:
            self.failed += 1
            self._save()

    def check(self) -> None:
        if self.sent >= self.budget:
            self.exhausted = True
            raise BudgetExhausted(
                f"MiniMax budget of {self.budget} calls reached ({self.sent} sent)"
            )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "model": MODEL,
            "sent": self.sent,
            "failed": self.failed,
            "by_phase": self.by_phase,
        }
        self._path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


# =============================================================================================
# The program: one predictor, rendered and parsed exactly as the harness does
# =============================================================================================


class ClassifyEvent(dspy.Signature):  # type: ignore[misc]
    """Replaced at run time by the v2 prompt's judging instructions."""

    session: str = dspy.InputField(desc="The session envelope as tagged JSON; trusted fact.")
    history: str = dspy.InputField(desc="Earlier events as tagged JSON, or empty.")
    event: str = dspy.InputField(desc="The event under judgement, as tagged JSON.")
    rationale: str = dspy.OutputField(desc="One to three sentences naming the deciding rules.")
    verdict: Literal["allow", "violation", "policy_silent"] = dspy.OutputField()
    rule_ids: list[str] = dspy.OutputField(desc="The rules the verdict rests on.")
    confidence: float = dspy.OutputField(desc="Probability that the verdict is correct.")


_REPAIR: contextvars.ContextVar[bool] = contextvars.ContextVar("repair", default=False)


class HarnessAdapter(dspy.Adapter):  # type: ignore[misc]
    """Formats a candidate as the harness formats a prompt file; parses with ``parse_decision``."""

    def __init__(
        self, frame: po.Frame, policy: Policy, identifiers: frozenset[str] = frozenset()
    ) -> None:
        super().__init__()
        self.frame = frame
        self.policy = policy
        # Train identifiers that instructions may not quote (see po.prepare_instructions).
        self.identifiers = identifiers

    def system_prompt(self, instructions: str, demos: Sequence[po.Demo]) -> str:
        template = po.assemble(self.frame, instructions, demos, identifiers=self.identifiers)
        return PromptTemplate.from_text(template, name="candidate", policy=self.policy).system

    def format(
        self, signature: Any, demos: list[Any], inputs: dict[str, Any]
    ) -> list[dict[str, Any]]:
        system = self.system_prompt(signature.instructions, [po.Demo.from_fields(d) for d in demos])
        user = po.user_message(
            inputs["session"],
            inputs.get("history") or "",
            inputs["event"],
            repair=REPAIR_INSTRUCTION if _REPAIR.get() else None,
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def parse(self, signature: Any, completion: str) -> dict[str, Any]:
        try:
            decision = parse_decision(completion, self.policy)
        except (ValueError, ValidationError) as exc:
            raise AdapterParseError(
                adapter_name="HarnessAdapter",
                signature=signature,
                lm_response=completion,
                message=str(exc)[:200],
            ) from exc
        return {
            "rationale": decision.rationale,
            "verdict": decision.verdict,
            "rule_ids": list(decision.rule_ids),
            "confidence": decision.confidence,
        }


@dataclass
class Runtime:
    """Set once in ``main``. Not module attributes: MIPROv2 deep-copies programs, and the
    ledger holds a lock, which can't be copied."""

    adapter: HarnessAdapter | None = None
    calls: CallLedger | None = None

    def get_adapter(self) -> HarnessAdapter:
        if self.adapter is None:
            raise RuntimeError("adapter not configured")
        return self.adapter

    def get_calls(self) -> CallLedger:
        if self.calls is None:
            raise RuntimeError("call ledger not configured")
        return self.calls


RUNTIME = Runtime()


def _is_rate_limit(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    while seen is not None:
        name = type(seen).__name__
        text = str(seen).lower()
        if "ratelimit" in name.lower() or "429" in text or "rate limit" in text:
            return True
        seen = seen.__cause__
    return False


class PolicyClassifier(dspy.Module):  # type: ignore[misc]
    """One judgement per event. Parse failures get one repair retry, then fail closed."""

    def __init__(self, instructions: str) -> None:
        super().__init__()
        self.predict = dspy.Predict(ClassifyEvent.with_instructions(instructions))

    def forward(self, session: str, history: str, event: str) -> dspy.Prediction:
        for repair in (False, True):
            token = _REPAIR.set(repair)
            try:
                return self._call(session=session, history=history, event=event)
            except AdapterParseError:
                continue
            finally:
                _REPAIR.reset(token)
        return dspy.Prediction(
            rationale="fail closed: unparseable output",
            verdict="policy_silent",
            rule_ids=[],
            confidence=0.0,
        )

    def _call(self, **inputs: str) -> dspy.Prediction:
        for _ in range(RATE_LIMIT_WAITS):
            RUNTIME.get_calls().check()
            try:
                with dspy.context(adapter=RUNTIME.get_adapter()):
                    return self.predict(**inputs)
            except AdapterParseError, BudgetExhausted:
                raise
            except Exception as exc:
                if not _is_rate_limit(exc):
                    raise
                logger.warning(
                    "rate limited after LiteLLM's retries; waiting %.0fs", RATE_LIMIT_WAIT_S
                )
                time.sleep(RATE_LIMIT_WAIT_S)
        raise RuntimeError("still rate limited after waiting; stopping")


def metric(example: dspy.Example, pred: dspy.Prediction, trace: Any = None) -> float | bool:
    """Graded score for selection; exact verdict and rules when choosing demos (trace set)."""
    gold, gold_rules = example.verdict, example.rule_ids
    if trace is not None:
        return po.strict_match(gold, gold_rules, pred.verdict, pred.rule_ids or [])
    return po.score(gold, gold_rules, pred.verdict, pred.rule_ids or [])


def feedback_metric(
    gold: dspy.Example,
    pred: dspy.Prediction,
    trace: Any = None,
    pred_name: str | None = None,
    pred_trace: Any = None,
    program_trace: Any = None,
) -> dspy.Prediction:
    """GEPA's metric: the same score as ``metric``, and text on what went right or wrong."""
    answered = getattr(pred, "verdict", None) or "error"
    answered_rules = list(getattr(pred, "rule_ids", None) or [])
    score = po.score(gold.verdict, gold.rule_ids, answered, answered_rules)
    feedback = po.feedback_text(
        score=score,
        gold=gold.verdict,
        gold_rules=gold.rule_ids,
        gold_rationale=gold.rationale,
        label_confidence=gold.label_confidence,
        predicted=answered,
        predicted_rules=answered_rules,
        predicted_rationale=str(getattr(pred, "rationale", "") or ""),
    )
    return dspy.Prediction(score=score, feedback=feedback)


# =============================================================================================
# Data
# =============================================================================================


def to_example(case: LabeledCase) -> dspy.Example:
    """A train case in the single-event setting; the gold answer doubles as a labelled demo."""
    return dspy.Example(
        **po.case_inputs(case.request.without_history()),
        rationale=case.rationale,
        verdict=case.label,
        rule_ids=list(case.rule_ids),
        confidence=po.DEMO_CONFIDENCE[case.label_confidence],
        label_confidence=case.label_confidence,
        case_id=case.id,
    ).with_inputs("session", "history", "event")


def _train_builder() -> ModuleType:
    """data/build_train_cases.py, for its eval near-duplicate index."""
    path = REPO_ROOT / "data" / "build_train_cases.py"
    spec = importlib.util.spec_from_file_location("build_train_cases", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# =============================================================================================
# Running
# =============================================================================================


def evaluate(program: dspy.Module, examples: list[dspy.Example], threads: int) -> dict[str, Any]:
    result = dspy.Evaluate(
        devset=examples,
        metric=metric,
        num_threads=threads,
        display_progress=True,
        max_errors=len(examples),
        failure_score=0.0,
    )(program)
    scores = [float(s) for _, _, s in result.results]
    pairs = [(ex.verdict, getattr(pred, "verdict", "error")) for ex, pred, _ in result.results]
    summary = po.summarise(scores, pairs)
    summary["errors"] = [
        ex.case_id for ex, pred, _ in result.results if not hasattr(pred, "verdict")
    ]
    summary["wrong"] = sorted(
        f"{ex.case_id}: {ex.verdict}->{getattr(pred, 'verdict', 'error')}"
        for ex, pred, _ in result.results
        if getattr(pred, "verdict", None) != ex.verdict
    )
    return summary


def export(
    program: dspy.Module, frame: po.Frame, out: Path, adapter: HarnessAdapter
) -> dict[str, Any]:
    """Write the chosen candidate as a prompt file and prove it renders as optimised."""
    instructions = program.predict.signature.instructions
    demos = [po.Demo.from_fields(d) for d in program.predict.demos]
    demo_ids = [str(getattr(d, "case_id", "")) or "bootstrapped" for d in program.predict.demos]
    if demos:
        index = _train_builder().EvalIndex(_eval_cases())
        for demo in demos:
            # Tagged JSON escapes <, > and & as < etc., which json.loads reads back.
            if index.near_duplicate(TraceEvent.model_validate(json.loads(demo.event))) is not None:
                raise ValueError("a demo is a near-duplicate of an eval event")
    _, removed = po.prepare_instructions(instructions, adapter.identifiers)
    text = po.assemble(frame, instructions, demos, identifiers=adapter.identifiers)
    out.write_text(text, encoding="utf-8")
    exported = PromptTemplate.from_text(out.read_text(), name=out.stem, policy=adapter.policy)
    assert exported.system == adapter.system_prompt(instructions, demos)
    return {
        "path": str(out.relative_to(REPO_ROOT)) if out.is_relative_to(REPO_ROOT) else str(out),
        "version": exported.version,
        "demos": len(demos),
        "demo_case_ids": demo_ids,
        "identifier_lines_removed": removed,
    }


def _eval_cases() -> list[LabeledCase]:
    """Read only to check demos against them; never scored here. Not read without demos."""
    settings = Settings()
    cases: list[LabeledCase] = []
    for path in (settings.cases_path, settings.hard_cases_path, settings.stress_cases_path):
        cases += load_cases(path)
    return cases


PROPOSER_CONTEXT = """\
Context for anyone writing instructions for this classifier. Its prompt is assembled as: a fixed
role sentence; the policy below, verbatim; THE INSTRUCTIONS BEING WRITTEN; up to four worked
examples, chosen separately from labelled data; and a fixed output section that requires one
JSON object with the keys rationale, verdict (allow | violation | policy_silent), rule_ids and
confidence. So the instructions must not restate, paraphrase, renumber or invent rules (refer
to them by the ids the policy defines), must not specify any output format, and must not
contain examples of their own (sections headed as output formats or examples are removed).
The policy:

<policy>
{policy}
</policy>"""

GEPA_CONTEXT = (
    PROPOSER_CONTEXT
    + """

The labelled examples you will see are train cases. Their orders, customers, email addresses
and amounts are specific to them, so the instructions must state general rules instead:
never quote one of those identifiers, and lines that do are removed."""
)


class PolicyAwareLM(dspy.LM):  # type: ignore[misc]
    """A reflection or proposal LM. Every request carries the policy and how the prompt is
    assembled, whether it is sent as messages (MIPROv2's proposer) or as one string (GEPA's
    reflection).

    MIPROv2's proposer sees only the current instructions and some examples, not the policy,
    which sits in the fixed part of the prompt. Without it, a first run proposed instructions
    that invented rules (a $100 cap, tools Lark doesn't have) and a non-JSON answer format.
    Every request also counts against the call ledger, and is labelled with ``phase``.
    """

    def __init__(self, *args: Any, context: str, phase: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context = context
        self.phase = phase

    def __call__(self, prompt: Any = None, *, messages: Any = None, **kwargs: Any) -> Any:
        if messages:
            if messages[0].get("role") == "system":
                head = {**messages[0], "content": f"{self.context}\n\n{messages[0]['content']}"}
                messages = [head, *messages[1:]]
            else:
                messages = [{"role": "system", "content": self.context}, *messages]
        elif prompt is not None:
            prompt = f"{self.context}\n\n{prompt}"
        if RUNTIME.calls is not None:
            RUNTIME.calls.check()
        with _labelled(self.phase):
            return super().__call__(prompt, messages=messages, **kwargs)


@contextlib.contextmanager
def _labelled(phase: str | None) -> Iterator[None]:
    """Label the ledger's calls made inside the block; the search is sequential, so no race."""
    calls = RUNTIME.calls
    if phase is None or calls is None:
        yield
        return
    previous, calls.phase = calls.phase, phase
    try:
        yield
    finally:
        calls.phase = previous


def configure(
    ledger_path: Path, budget: int, threads: int, policy: Policy, optimizer: str
) -> tuple[dspy.LM, PolicyAwareLM]:
    """(task model, reflection or proposal model): both MiniMax-M3; the second sees the policy."""
    settings = Settings()
    if settings.minimax_api_key is None:
        raise SystemExit("MINIMAX_API_KEY is not set (put it in .env)")
    RUNTIME.calls = CallLedger(ledger_path, budget)
    litellm.callbacks = [RUNTIME.calls]
    dspy.configure_cache(
        enable_disk_cache=True, enable_memory_cache=True, disk_cache_dir=str(CACHE_DIR)
    )
    common: dict[str, Any] = {
        "engine": "litellm",
        "api_base": settings.minimax_base_url,
        "api_key": settings.minimax_api_key.get_secret_value(),
        "num_retries": 4,
        "extra_body": EXTRA_BODY,
    }
    lm = dspy.LM(f"openai/{MODEL}", max_tokens=settings.max_output_tokens, **common)
    gepa = optimizer == "gepa"
    proposer = PolicyAwareLM(
        f"openai/{MODEL}",
        max_tokens=PROPOSER_MAX_TOKENS,
        context=(GEPA_CONTEXT if gepa else PROPOSER_CONTEXT).format(policy=policy.text.strip()),
        phase="gepa-reflection" if gepa else "miprov2-proposer",
        **common,
    )
    dspy.configure(lm=lm, num_threads=threads, max_errors=50)
    return lm, proposer


def run_miprov2(
    baseline: PolicyClassifier,
    trainset: list[dspy.Example],
    valset: list[dspy.Example],
    lm: dspy.LM,
    proposer: dspy.LM,
    threads: int,
    seed: int,
    identifiers: frozenset[str],
) -> tuple[dspy.Module, dict[str, Any]]:
    optimizer = dspy.MIPROv2(
        metric=metric,
        prompt_model=proposer,
        task_model=lm,
        auto="light",
        max_bootstrapped_demos=po.MAX_DEMOS,
        max_labeled_demos=po.MAX_DEMOS,
        num_threads=threads,
        max_errors=50,
        seed=seed,
        log_dir=str(REPO_ROOT / "results" / "scratch" / "mipro_logs"),
    )
    best = optimizer.compile(baseline, trainset=trainset, valset=valset)
    section: dict[str, Any] = {
        "auto": "light",
        "best_validation_metric": best.score,
        "candidates": [
            {
                "validation_metric": c["score"],
                "instructions": po.prepare_instructions(
                    c["program"].predict.signature.instructions, identifiers
                )[0],
                "identifier_lines_found": len(
                    po.find_identifier_lines(
                        po.sanitise_instructions(c["program"].predict.signature.instructions),
                        identifiers,
                    )
                ),
                "demos": len(c["program"].predict.demos),
            }
            for c in getattr(best, "candidate_programs", [])
        ],
    }
    return best, section


def run_gepa(
    baseline: PolicyClassifier,
    trainset: list[dspy.Example],
    valset: list[dspy.Example],
    reflection: dspy.LM,
    threads: int,
    seed: int,
    metric_calls: int,
    state_dir: Path,
    identifiers: frozenset[str],
) -> tuple[dspy.Module, dict[str, Any]]:
    """GEPA evolves the instructions only (the baseline has no demos). It reflects on minibatches
    of the train side and selects on the validation side, which it scores in full per candidate.
    """
    optimizer = dspy.GEPA(
        metric=feedback_metric,
        reflection_lm=reflection,
        max_metric_calls=metric_calls,
        reflection_minibatch_size=3,
        use_merge=False,
        num_threads=threads,
        seed=seed,
        track_stats=True,
        # GEPA resumes from this directory if it holds a run: keep it empty for a fresh run.
        log_dir=str(state_dir),
    )
    best = optimizer.compile(baseline, trainset=trainset, valset=valset)
    stats = best.detailed_results
    section: dict[str, Any] = {
        "max_metric_calls": metric_calls,
        "reflection_minibatch_size": 3,
        "use_merge": False,
        "best_validation_metric": stats.val_aggregate_scores[stats.best_idx],
        "candidates": [
            {
                "validation_metric": round(score, 4),
                "instructions": po.prepare_instructions(
                    program.predict.signature.instructions, identifiers
                )[0],
                "identifier_lines_found": len(
                    po.find_identifier_lines(
                        po.sanitise_instructions(program.predict.signature.instructions),
                        identifiers,
                    )
                ),
                "demos": len(program.predict.demos),
            }
            for program, score in zip(stats.candidates, stats.val_aggregate_scores, strict=True)
        ],
        "best_idx": stats.best_idx,
    }
    return best, section


def _stop_if_exhausted(calls: CallLedger) -> None:
    if calls.exhausted:
        raise SystemExit(
            f"MiniMax budget of {calls.budget} calls reached during the search: nothing was "
            "exported. Rerun with a larger --budget; completed calls are cached, so they are free."
        )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--optimizer", choices=OPTIMIZERS, default="miprov2")
    parser.add_argument("--out", type=Path, default=None, help="prompt file to write")
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument(
        "--base-prompt", type=Path, default=REPO_ROOT / "prompts" / "classifier_v2.md"
    )
    parser.add_argument("--budget", type=int, default=None, help="stop before this many calls")
    parser.add_argument(
        "--gepa-metric-calls",
        type=int,
        default=400,
        help="GEPA's metric budget (each is one classifier call); auto=light would be ~640",
    )
    parser.add_argument("--threads", type=int, default=2, help="concurrent requests (<= 2)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--gepa-state-dir",
        type=Path,
        default=REPO_ROOT / "results" / "scratch" / "gepa_logs",
        help="where GEPA keeps its search state",
    )
    parser.add_argument(
        "--resume", action="store_true", help="continue the GEPA run in --gepa-state-dir"
    )
    parser.add_argument("--skip-train-eval", action="store_true", help="save ~200 calls")
    parser.add_argument("--dry-run", action="store_true", help="print the split; no model calls")
    args = parser.parse_args(argv)
    args.budget = args.budget if args.budget is not None else DEFAULT_BUDGET[args.optimizer]
    args.report_dir = args.report_dir or REPO_ROOT / "results" / "optimization" / args.optimizer
    args.report_dir = args.report_dir.resolve()
    args.out = (
        args.out or REPO_ROOT / "prompts" / f"classifier_v3_{OUTPUT_NAME[args.optimizer]}.md"
    ).resolve()
    return args


def _search(
    args: argparse.Namespace,
    baseline: PolicyClassifier,
    trainset: list[dspy.Example],
    valset: list[dspy.Example],
    lm: dspy.LM,
    proposer: dspy.LM,
    identifiers: frozenset[str],
) -> tuple[dspy.Module, dict[str, Any]]:
    """Run the chosen optimiser on the train side, selecting on the validation side."""
    threads = min(args.threads, 2)
    try:
        if args.optimizer == "miprov2":
            return run_miprov2(
                baseline, trainset, valset, lm, proposer, threads, args.seed, identifiers
            )
        return run_gepa(
            baseline,
            trainset,
            valset,
            proposer,
            threads,
            args.seed,
            args.gepa_metric_calls,
            args.gepa_state_dir,
            identifiers,
        )
    except BudgetExhausted:
        _stop_if_exhausted(RUNTIME.get_calls())
        raise


def _finish(
    args: argparse.Namespace,
    best: dspy.Module,
    baseline: PolicyClassifier,
    frame: po.Frame,
    trainset: list[dspy.Example],
    valset: list[dspy.Example],
    report: dict[str, Any],
) -> None:
    """Export the chosen candidate, score it and the baseline on the splits, and write a report."""
    calls = RUNTIME.get_calls()
    threads = min(args.threads, 2)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    best.save(str(args.report_dir / "program.json"))
    report["exported"] = export(best, frame, args.out, RUNTIME.get_adapter())

    calls.phase = "v3-validation"
    report["v3_validation"] = evaluate(best, valset, threads)
    if not args.skip_train_eval:
        calls.phase = "train-eval"
        report["v2_train"] = evaluate(baseline, trainset, threads)
        report["v3_train"] = evaluate(best, trainset, threads)
    _stop_if_exhausted(calls)
    report["calls"] = {"sent": calls.sent, "failed": calls.failed, "by_phase": calls.by_phase}
    (args.report_dir / "report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n"
    )
    logger.info(
        "wrote %s and %s; %d MiniMax calls in the ledger",
        args.out,
        args.report_dir / "report.json",
        calls.sent,
    )


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    settings = Settings()
    policy = load_policy(settings.policy_path)
    base_text = args.base_prompt.read_text(encoding="utf-8")
    frame = po.Frame.split(base_text)
    cases = load_cases(settings.train_cases_path)
    identifiers = po.case_identifiers(cases, base_text + policy.text)
    train, val = po.holdout_split(cases, seed=args.seed)
    held_out = sorted({po.template_of(c) for c in val})
    split = {
        "train": len(train),
        "validation": len(val),
        "validation_templates": held_out,
        "identifier_terms": len(identifiers),
    }
    logger.info(
        "train %d cases, validation %d cases (%d templates); %d identifiers guarded",
        len(train),
        len(val),
        len(held_out),
        len(identifiers),
    )
    if args.dry_run:
        print(json.dumps(split, indent=1))
        return 0

    if args.optimizer == "gepa" and not args.resume and any(args.gepa_state_dir.glob("*")):
        raise SystemExit(
            f"{args.gepa_state_dir} holds an earlier GEPA run: pass --resume to continue it, "
            "or empty the directory for a fresh run"
        )
    RUNTIME.adapter = HarnessAdapter(frame, policy, identifiers)
    lm, proposer = configure(
        args.report_dir / "minimax_calls.json",
        args.budget,
        min(args.threads, 2),
        policy,
        args.optimizer,
    )
    calls = RUNTIME.get_calls()
    trainset, valset = [to_example(c) for c in train], [to_example(c) for c in val]
    baseline = PolicyClassifier(frame.instructions)

    calls.phase = "baseline-validation"
    report: dict[str, Any] = {
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "optimizer": args.optimizer,
        "split": split,
    }
    report["v2_validation"] = evaluate(baseline, valset, min(args.threads, 2))

    calls.phase = args.optimizer
    best, section = _search(args, baseline, trainset, valset, lm, proposer, identifiers)
    _stop_if_exhausted(calls)
    report[args.optimizer] = section
    _finish(args, best, baseline, frame, trainset, valset, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
