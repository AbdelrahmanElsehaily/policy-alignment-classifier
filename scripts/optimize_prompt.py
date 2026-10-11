"""Optimise the classifier prompt with DSPy (MIPROv2, light) on the train split, then export it.

    uv sync --group optimize
    uv run --group optimize python scripts/optimize_prompt.py --dry-run     # sizes, no calls
    uv run --group optimize python scripts/optimize_prompt.py --out prompts/classifier_v3.md

What is optimised. The middle of prompts/classifier_v2.md (the "How to judge" and "Untrusted
content" instructions) and up to four worked examples. The head (role, ``$policy``) and the
tail (output contract, ``$schema``) stay fixed, so the policy, the schema and the parser are
untouched. Nothing is fine-tuned: the model's weights never change.

On what. ``data/train_cases.jsonl`` only, single-event setting. A quarter of its templates
(whole templates, so rephrasings of one idea never straddle the split) are held out as the
validation set MIPROv2 selects on. Dev, test, hard and stress are never read here.

How the prompt reaches the model. A custom DSPy adapter renders each candidate exactly as the
harness renders the exported file: the system prompt through ``PromptTemplate.from_text`` and
the user turn through ``prompt_optimization.user_message`` (tested byte-identical to
``PromptTemplate.render``), parsed with ``parse_decision``, one repair retry, then fail closed
to ``policy_silent``, like ``LLMClassifier``. What DSPy scores is what ``pac eval`` will run.

Model and budget. MiniMax-M3 only, through its OpenAI-compatible API via LiteLLM, thinking
disabled. DSPy's response cache (``results/scratch/dspy_cache``) makes reruns free. Every
request LiteLLM actually sends, retries included, is counted in a ledger
(``results/optimization/minimax_calls.json``) that persists across runs; the script stops
before ``--budget`` is exceeded. HTTP 429s are retried by LiteLLM with backoff, then waited
out here.
"""

from __future__ import annotations

import argparse
import contextvars
import importlib.util
import json
import logging
import sys
import threading
import time
from collections.abc import Sequence
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
LEDGER = REPO_ROOT / "results" / "optimization" / "minimax_calls.json"
RATE_LIMIT_WAIT_S = 60.0
RATE_LIMIT_WAITS = 10

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

    def __init__(self, frame: po.Frame, policy: Policy) -> None:
        super().__init__()
        self.frame = frame
        self.policy = policy

    def system_prompt(self, instructions: str, demos: Sequence[po.Demo]) -> str:
        template = po.assemble(self.frame, instructions, demos)
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


def export(program: PolicyClassifier, frame: po.Frame, out: Path) -> dict[str, Any]:
    """Write the chosen candidate as a prompt file and prove it renders as optimised."""
    adapter = RUNTIME.get_adapter()
    instructions = program.predict.signature.instructions
    demos = [po.Demo.from_fields(d) for d in program.predict.demos]
    index = _train_builder().EvalIndex(_eval_cases())
    demo_ids = [str(getattr(d, "case_id", "")) or "bootstrapped" for d in program.predict.demos]
    for demo in demos:
        # Tagged JSON escapes <, > and & as < etc., which json.loads reads back.
        if index.near_duplicate(TraceEvent.model_validate(json.loads(demo.event))) is not None:
            raise ValueError("a demo is a near-duplicate of an eval event")
    text = po.assemble(frame, instructions, demos)
    out.write_text(text, encoding="utf-8")
    exported = PromptTemplate.from_text(out.read_text(), name=out.stem, policy=adapter.policy)
    assert exported.system == adapter.system_prompt(instructions, demos)
    return {
        "path": str(out.relative_to(REPO_ROOT)),
        "version": exported.version,
        "demos": len(demos),
        "demo_case_ids": demo_ids,
    }


def _eval_cases() -> list[LabeledCase]:
    """Read only to check demos against them; never scored here."""
    settings = Settings()
    cases: list[LabeledCase] = []
    for path in (settings.cases_path, settings.hard_cases_path, settings.stress_cases_path):
        cases += load_cases(path)
    return cases


PROPOSER_CONTEXT = """\
Context for anyone writing instructions for this classifier. Its prompt is assembled as: a fixed
role sentence; the policy below, verbatim; THE INSTRUCTIONS BEING WRITTEN; optional worked
examples; and a fixed output section that requires one JSON object with the keys rationale,
verdict (allow | violation | policy_silent), rule_ids and confidence. So the instructions must
not restate, paraphrase, renumber or invent rules (refer to them by the ids the policy defines),
and must not specify any output format. The policy:

<policy>
{policy}
</policy>"""


class PolicyAwareLM(dspy.LM):  # type: ignore[misc]
    """The proposer's LM. Every request carries the policy and how the prompt is assembled.

    MIPROv2's proposer sees only the current instructions and some examples, not the policy,
    which sits in the fixed part of the prompt. Without it, a first run proposed instructions
    that invented rules (a $100 cap, tools Lark doesn't have) and a non-JSON answer format.
    """

    def __init__(self, *args: Any, context: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.context = context

    def __call__(self, prompt: Any = None, *, messages: Any = None, **kwargs: Any) -> Any:
        if messages and messages[0].get("role") == "system":
            first = {**messages[0], "content": f"{self.context}\n\n{messages[0]['content']}"}
            messages = [first, *messages[1:]]
        return super().__call__(prompt, messages=messages, **kwargs)


def configure(budget: int, threads: int, policy: Policy) -> tuple[dspy.LM, dspy.LM]:
    """(task model, proposer model): the same MiniMax-M3, the proposer with the policy."""
    settings = Settings()
    if settings.minimax_api_key is None:
        raise SystemExit("MINIMAX_API_KEY is not set (put it in .env)")
    RUNTIME.calls = CallLedger(LEDGER, budget)
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
    # Longer outputs: a full instruction set doesn't fit in the classifier's 1,024 tokens.
    proposer = PolicyAwareLM(
        f"openai/{MODEL}",
        max_tokens=4096,
        context=PROPOSER_CONTEXT.format(policy=policy.text.strip()),
        **common,
    )
    dspy.configure(lm=lm, num_threads=threads, max_errors=50)
    return lm, proposer


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "prompts" / "classifier_v3.md")
    parser.add_argument("--report-dir", type=Path, default=REPO_ROOT / "results" / "optimization")
    parser.add_argument(
        "--base-prompt", type=Path, default=REPO_ROOT / "prompts" / "classifier_v2.md"
    )
    parser.add_argument("--budget", type=int, default=1100, help="stop before this many calls")
    parser.add_argument("--threads", type=int, default=2, help="concurrent requests (<= 2)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--skip-train-eval", action="store_true", help="save ~400 calls")
    parser.add_argument("--dry-run", action="store_true", help="print the split; no model calls")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)

    settings = Settings()
    policy = load_policy(settings.policy_path)
    frame = po.Frame.split(args.base_prompt.read_text(encoding="utf-8"))
    cases = load_cases(settings.train_cases_path)
    train, val = po.holdout_split(cases, seed=args.seed)
    held_out = sorted({po.template_of(c) for c in val})
    split = {"train": len(train), "validation": len(val), "validation_templates": held_out}
    logger.info(
        "train %d cases, validation %d cases (%d templates)",
        len(train),
        len(val),
        len(held_out),
    )
    if args.dry_run:
        print(json.dumps(split, indent=1))
        return 0

    RUNTIME.adapter = HarnessAdapter(frame, policy)
    lm, proposer = configure(args.budget, min(args.threads, 2), policy)
    calls = RUNTIME.get_calls()
    trainset, valset = [to_example(c) for c in train], [to_example(c) for c in val]
    baseline = PolicyClassifier(frame.instructions)

    calls.phase = "baseline-validation"
    report: dict[str, Any] = {
        "started": datetime.now(UTC).isoformat(timespec="seconds"),
        "split": split,
    }
    report["v2_validation"] = evaluate(baseline, valset, args.threads)

    calls.phase = "miprov2"
    optimizer = dspy.MIPROv2(
        metric=metric,
        prompt_model=proposer,
        task_model=lm,
        auto="light",
        max_bootstrapped_demos=po.MAX_DEMOS,
        max_labeled_demos=po.MAX_DEMOS,
        num_threads=min(args.threads, 2),
        max_errors=50,
        seed=args.seed,
        log_dir=str(REPO_ROOT / "results" / "scratch" / "mipro_logs"),
    )
    best = optimizer.compile(baseline, trainset=trainset, valset=valset)
    report["miprov2"] = {
        "auto": "light",
        "best_validation_metric": best.score,
        "candidates": [
            {
                "validation_metric": c["score"],
                "instructions": c["program"].predict.signature.instructions,
                "demos": len(c["program"].predict.demos),
            }
            for c in getattr(best, "candidate_programs", [])
        ],
    }
    best.save(str(args.report_dir / "program.json"))
    report["exported"] = export(best, frame, args.out)

    calls.phase = "v3-validation"
    report["v3_validation"] = evaluate(best, valset, args.threads)
    if not args.skip_train_eval:
        calls.phase = "train-eval"
        report["v2_train"] = evaluate(baseline, trainset, args.threads)
        report["v3_train"] = evaluate(best, trainset, args.threads)
    report["calls"] = {"sent": calls.sent, "failed": calls.failed, "by_phase": calls.by_phase}
    args.report_dir.mkdir(parents=True, exist_ok=True)
    (args.report_dir / "report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n"
    )
    logger.info(
        "wrote %s and %s; %d MiniMax calls in the ledger",
        args.out,
        args.report_dir / "report.json",
        calls.sent,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
