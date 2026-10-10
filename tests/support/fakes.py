"""A deterministic in-process backend for tests and offline development."""

from __future__ import annotations

from collections.abc import Callable

from pac.guardrail.application.ports import Completion, LLMRequest
from pac.guardrail.domain.classification import Usage

Responder = Callable[[LLMRequest], str | Exception]


class FakeBackend:
    """Answers with whatever ``responder`` returns, and records every request it receives.

    A responder that returns an exception makes :meth:`complete` raise it, which is how tests
    exercise timeouts, refusals, and provider errors.
    """

    def __init__(self, responder: Responder, *, model: str = "fake-model") -> None:
        self._responder = responder
        self._model = model
        self.requests: list[LLMRequest] = []

    @property
    def model(self) -> str:
        return self._model

    async def complete(self, request: LLMRequest) -> Completion:
        self.requests.append(request)
        result = self._responder(request)
        if isinstance(result, Exception):
            raise result
        return Completion(
            text=result,
            model=self._model,
            usage=Usage(input_tokens=len(request.system + request.user) // 4, output_tokens=40),
        )
