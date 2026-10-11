"""Regression guard: every committed eval run must reproduce exactly from the response cache.

For each run under results/runs, rebuild the classifier the run used (same prompt file and
hash, same policy, same model id), replay the recorded completions, and require identical
verdicts and identical metrics. Any change to prompt rendering, parsing, schemas, the dataset
or the metrics code that would alter a reported number fails here, in CI, offline.
"""

from __future__ import annotations

import json

import pytest

from pac.evaluation.application.run_eval import run_cases
from pac.evaluation.domain.metrics import compute_metrics
from pac.evaluation.domain.run import RunConfig
from pac.evaluation.infrastructure.case_store import file_sha256, load_cases
from pac.evaluation.infrastructure.run_store import FileRunStore
from pac.guardrail.application.llm_classifier import LLMClassifier
from pac.guardrail.domain.classification import EventClassifier
from pac.guardrail.infrastructure.files import load_policy, load_prompt
from pac.guardrail.infrastructure.llm.caching import CachingBackend
from pac.guardrail.infrastructure.regex_classifier import RegexBaseline
from pac.settings import REPO_ROOT, Settings

pytestmark = pytest.mark.integration

# Fixed repo paths, deliberately not read from the environment or a developer's .env: this
# test is about the committed artefacts, wherever the local config points.
SETTINGS = Settings(
    policy_path=REPO_ROOT / "policy" / "policy_v0.md",
    cases_path=REPO_ROOT / "data" / "cases.jsonl",
    cache_dir=REPO_ROOT / "cache",
    results_dir=REPO_ROOT / "results",
)
STORE = FileRunStore(SETTINGS.results_dir / "runs")
RUNS = STORE.list_ids()


def _classifier_for(config: RunConfig) -> EventClassifier:
    if config.backend == "regex":
        return RegexBaseline()
    policy = load_policy(SETTINGS.policy_path)
    assert policy.sha256 == config.policy_sha256, "policy changed since this run was recorded"
    stem = config.prompt_version.split("@")[0]
    prompt = load_prompt(
        REPO_ROOT / "prompts" / f"{stem}.md", policy, temperature=config.temperature
    )
    assert prompt.version == config.prompt_version, f"{stem}.md changed since this run"
    replay = CachingBackend(SETTINGS.cache_dir, inner=None, model=config.classifier)
    return LLMClassifier(replay, policy, prompt)


@pytest.mark.skipif(not RUNS, reason="no committed runs")
@pytest.mark.parametrize("run_id", RUNS)
async def test_committed_run_reproduces(run_id: str) -> None:
    saved = STORE.load(run_id)
    config, recorded, metrics = saved.config, saved.predictions, saved.metrics
    recomputed = compute_metrics(recorded)
    assert json.loads(recomputed.model_dump_json()) == json.loads(metrics.model_dump_json())
    errors = metrics.operational.classifier_errors
    assert (errors.value or 0) <= 0.1, "a committed run that mostly failed closed measures nothing"
    if config.repeats > 1:
        pytest.skip("variance runs bypass the cache by design; only their metrics are checked")
    dataset = REPO_ROOT / "data" / config.dataset
    assert file_sha256(dataset) == config.dataset_sha256, (
        "dataset changed since this run was recorded; re-run the eval to refresh it"
    )
    by_id = {c.id: c for c in load_cases(dataset)}
    cases = [by_id[p.case_id] for p in recorded if p.repeat == 0]

    replayed = await run_cases(
        _classifier_for(config), cases, with_history=config.with_history, concurrency=8
    )

    expected = {p.case_id: p for p in recorded if p.repeat == 0}
    for p in replayed:
        before = expected[p.case_id].result
        if before.error is not None:
            # Failures are never cached; replay must still fail closed the same way.
            assert p.result.decision.verdict == before.decision.verdict, p.case_id
            assert p.result.error is not None, p.case_id
        else:
            assert p.result.decision == before.decision, p.case_id
