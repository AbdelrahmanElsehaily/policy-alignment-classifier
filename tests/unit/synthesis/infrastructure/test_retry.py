"""Transient provider failures are retried; real errors and budget stops are not."""

from __future__ import annotations

import pytest

from pac.guardrail.application.ports import BackendError, LLMRequest
from pac.synthesis.infrastructure.ledger import BudgetExceeded
from pac.synthesis.infrastructure.retry import RetryingBackend, transient
from tests.support.fakes import FakeBackend

REQUEST = LLMRequest(system="s", user="u", json_schema={}, max_tokens=10)


async def _no_sleep(_: float) -> None:
    return None


def test_what_counts_as_transient() -> None:
    assert transient(BackendError('MiniMax-M3 HTTP 529: {"type":"overloaded_error"}'))
    assert transient(BackendError("m HTTP 502: bad gateway"))
    assert not transient(BackendError("m HTTP 400: bad request"))
    assert not transient(BudgetExceeded("minimax: 6000 calls made"))


async def test_retries_an_overload_then_succeeds() -> None:
    answers: list[str | Exception] = [BackendError("HTTP 529 overloaded"), "ok"]
    inner = FakeBackend(lambda _: answers.pop(0))
    result = await RetryingBackend(inner, sleep=_no_sleep).complete(REQUEST)
    assert result.text == "ok"
    assert len(inner.requests) == 2


async def test_gives_up_on_a_permanent_error_at_once() -> None:
    inner = FakeBackend(lambda _: BackendError("HTTP 400: invalid"))
    with pytest.raises(BackendError):
        await RetryingBackend(inner, sleep=_no_sleep).complete(REQUEST)
    assert len(inner.requests) == 1


async def test_stops_after_the_last_attempt() -> None:
    inner = FakeBackend(lambda _: BackendError("HTTP 503"))
    with pytest.raises(BackendError):
        await RetryingBackend(inner, attempts=3, sleep=_no_sleep).complete(REQUEST)
    assert len(inner.requests) == 3
