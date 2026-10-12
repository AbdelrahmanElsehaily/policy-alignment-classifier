"""Retry transient provider failures (overload, 5xx, dropped connections) with backoff.

The guardrail's adapters retry only rate limits (429), which is right on the request path: a
guardrail should fail closed fast. A batch pipeline should wait instead: MiniMax answers HTTP
529 "overloaded" in bursts, and a juror or a simulated agent failing on one would lose a ballot
or cut a conversation short. Each retry is a new call, so it is counted by the ledger beneath.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from pac.guardrail.application.ports import (
    BackendError,
    Completion,
    LLMBackend,
    LLMRequest,
    NonRetryableBackendError,
)

logger = logging.getLogger(__name__)

TRANSIENT = ("HTTP 5", "overloaded", "request failed", "timed out", "timeout", "high load")


def transient(error: BackendError) -> bool:
    if isinstance(error, NonRetryableBackendError):
        return False
    text = str(error)
    return any(marker in text for marker in TRANSIENT)


class RetryingBackend:
    def __init__(
        self,
        inner: LLMBackend,
        *,
        attempts: int = 5,
        base_delay_s: float = 4.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._inner = inner
        self._attempts = attempts
        self._base = base_delay_s
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._inner.model

    async def complete(self, request: LLMRequest) -> Completion:
        for attempt in range(1, self._attempts + 1):
            try:
                return await self._inner.complete(request)
            except BackendError as exc:
                if attempt == self._attempts or not transient(exc):
                    raise
                wait = min(self._base * 2 ** (attempt - 1), 60.0)
                logger.warning(
                    "transient provider error; retrying",
                    extra={
                        "model": self.model,
                        "attempt": attempt,
                        "wait_s": wait,
                        "reason": str(exc)[:120],
                    },
                )
                await self._sleep(wait)
        raise AssertionError("unreachable")
