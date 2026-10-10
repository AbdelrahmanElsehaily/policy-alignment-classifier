from __future__ import annotations

import asyncio
from pathlib import Path

from pac.backends import BackendError, CachingBackend, LLMRequest
from pac.backends.fake import FakeBackend
from pac.classifier import LLMClassifier
from pac.policy import Policy
from pac.prompting import REPAIR_INSTRUCTION, PromptTemplate
from pac.schemas import ClassificationRequest
from tests.conftest import decision_json


def _classifier(
    backend: FakeBackend | CachingBackend,
    policy: Policy,
    prompt: PromptTemplate,
    timeout_s: float = 5.0,
) -> LLMClassifier:
    return LLMClassifier(backend, policy, prompt, timeout_s=timeout_s)


async def test_happy_path(
    policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    fake = FakeBackend(lambda _: decision_json("violation", ["R5"], 0.95))
    result = await _classifier(fake, policy, prompt).classify(refund_request)

    assert result.decision.verdict == "violation"
    assert result.decision.rule_ids == ("R5",)
    assert result.error is None
    assert result.policy_version == "v0"
    assert result.prompt_version == prompt.version
    assert result.usage.input_tokens > 0
    assert len(fake.requests) == 1


async def test_repairs_unparseable_output_once(
    policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    def responder(request: LLMRequest) -> str:
        return decision_json("violation", ["R5"]) if REPAIR_INSTRUCTION in request.user else "hm"

    fake = FakeBackend(responder)
    result = await _classifier(fake, policy, prompt).classify(refund_request)

    assert result.decision.verdict == "violation"
    assert len(fake.requests) == 2
    assert result.usage.output_tokens == 80  # both attempts are billed


async def test_fails_closed_after_repeated_garbage(
    policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    result = await _classifier(FakeBackend(lambda _: "nope"), policy, prompt).classify(
        refund_request
    )
    assert result.decision.verdict == "policy_silent"
    assert result.decision.confidence == 0.0
    assert result.error is not None
    assert "unparseable" in result.error


async def test_retries_provider_errors_then_fails_closed(
    policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    fake = FakeBackend(lambda _: BackendError("HTTP 529 overloaded"))
    result = await _classifier(fake, policy, prompt).classify(refund_request)
    assert result.decision.verdict == "policy_silent"
    assert result.error == "HTTP 529 overloaded"
    assert len(fake.requests) == 2


async def test_recovers_from_transient_provider_error(
    policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    calls: list[str | Exception] = [BackendError("blip"), decision_json()]
    pending = iter(calls)
    result = await _classifier(FakeBackend(lambda _: next(pending)), policy, prompt).classify(
        refund_request
    )
    assert result.decision.verdict == "allow"
    assert result.error is None


async def test_timeout_fails_closed(
    policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    class Slow(FakeBackend):
        async def complete(self, request: LLMRequest):  # type: ignore[no-untyped-def]
            await asyncio.sleep(1)
            return await super().complete(request)

    result = await _classifier(
        Slow(lambda _: decision_json()), policy, prompt, timeout_s=0.01
    ).classify(refund_request)
    assert result.decision.verdict == "policy_silent"
    assert result.error is not None
    assert "timed out" in result.error


async def test_replay_miss_is_not_retried(
    tmp_path: Path, policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    replay = CachingBackend(tmp_path, inner=None, model="m")
    result = await _classifier(replay, policy, prompt).classify(refund_request)
    assert result.decision.verdict == "policy_silent"
    assert result.error is not None
    assert "no recorded completion" in result.error


async def test_cached_flag_propagates(
    tmp_path: Path, policy: Policy, prompt: PromptTemplate, refund_request: ClassificationRequest
) -> None:
    backend = CachingBackend(tmp_path, inner=FakeBackend(lambda _: decision_json()))
    classifier = _classifier(backend, policy, prompt)
    assert not (await classifier.classify(refund_request)).cached
    assert (await classifier.classify(refund_request)).cached
