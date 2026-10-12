from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
import respx

from pac.bootstrap import build_backend
from pac.guardrail.application.ports import BackendError, LLMRequest
from pac.guardrail.infrastructure.llm.anthropic import AnthropicBackend
from pac.guardrail.infrastructure.llm.caching import CacheMiss, CachingBackend
from pac.guardrail.infrastructure.llm.ollama import OllamaBackend
from pac.guardrail.infrastructure.llm.openai_compatible import OpenAICompatibleBackend
from pac.settings import Settings
from tests.support.fakes import FakeBackend

REQUEST = LLMRequest(
    system="policy text", user="event", json_schema={"type": "object"}, max_tokens=64
)


# --- cache key -------------------------------------------------------------------------------


def test_cache_key_is_stable_and_sensitive() -> None:
    assert REQUEST.cache_key("m") == REQUEST.cache_key("m")
    assert REQUEST.cache_key("m") != REQUEST.cache_key("other")
    assert REQUEST.cache_key("m") != REQUEST.model_copy(update={"system": "x"}).cache_key("m")


def test_unset_temperature_keeps_the_recorded_keys() -> None:
    # The key recordings were made under, before requests carried a temperature.
    legacy = json.dumps(
        {"model": "m", **REQUEST.model_dump(exclude={"temperature"})},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert REQUEST.cache_key("m") == hashlib.sha256(legacy.encode()).hexdigest()
    pinned = REQUEST.model_copy(update={"temperature": 0.0})
    assert pinned.cache_key("m") != REQUEST.cache_key("m")
    assert pinned.cache_key("m") != pinned.model_copy(update={"temperature": 0.5}).cache_key("m")


# --- caching backend -------------------------------------------------------------------------


async def test_cache_records_then_replays(tmp_path: Path) -> None:
    fake = FakeBackend(lambda _: '{"ok": true}')
    cached = CachingBackend(tmp_path, inner=fake)

    first = await cached.complete(REQUEST)
    second = await cached.complete(REQUEST)

    assert not first.cached
    assert second.cached
    assert second.text == first.text
    assert len(fake.requests) == 1


async def test_replay_mode_reads_recordings_and_misses_loudly(tmp_path: Path) -> None:
    await CachingBackend(tmp_path, inner=FakeBackend(lambda _: "{}", model="m")).complete(REQUEST)

    replay = CachingBackend(tmp_path, inner=None, model="m")
    assert (await replay.complete(REQUEST)).cached
    with pytest.raises(CacheMiss):
        await replay.complete(REQUEST.model_copy(update={"user": "unseen"}))


def test_replay_mode_requires_model(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="model"):
        CachingBackend(tmp_path, inner=None)


async def test_cache_does_not_store_failures(tmp_path: Path) -> None:
    cached = CachingBackend(tmp_path, inner=FakeBackend(lambda _: BackendError("boom")))
    with pytest.raises(BackendError):
        await cached.complete(REQUEST)
    assert not list(tmp_path.rglob("*.json"))


# --- anthropic backend -----------------------------------------------------------------------


class _StubMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def _anthropic_response(stop_reason: str = "end_turn", text: str | None = '{"a": 1}') -> Any:
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    usage = SimpleNamespace(
        input_tokens=1200,
        output_tokens=80,
        cache_read_input_tokens=1000,
        cache_creation_input_tokens=None,
    )
    return SimpleNamespace(stop_reason=stop_reason, content=content, usage=usage)


def _anthropic_backend(messages: _StubMessages) -> AnthropicBackend:
    client: Any = SimpleNamespace(messages=messages)
    return AnthropicBackend("claude-haiku-5-5", client=client)


async def test_anthropic_request_shape_and_usage() -> None:
    messages = _StubMessages(_anthropic_response())
    completion = await _anthropic_backend(messages).complete(REQUEST)

    call = messages.calls[0]
    assert call["model"] == "claude-haiku-5-5"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"]["format"]["schema"] == REQUEST.json_schema
    assert call["output_config"]["effort"] == "low"
    assert completion.text == '{"a": 1}'
    assert completion.usage.cache_read_input_tokens == 1000
    assert completion.usage.cache_creation_input_tokens == 0


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_anthropic_response(stop_reason="refusal"), "refused"),
        (_anthropic_response(stop_reason="max_tokens"), "max_tokens"),
        (_anthropic_response(text=None), "no text"),
    ],
)
async def test_anthropic_unusable_responses_raise(response: Any, message: str) -> None:
    with pytest.raises(BackendError, match=message):
        await _anthropic_backend(_StubMessages(response)).complete(REQUEST)


async def test_anthropic_api_errors_become_backend_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.APIConnectionError(request=request)
    with pytest.raises(BackendError, match="connection"):
        await _anthropic_backend(_StubMessages(error=error)).complete(REQUEST)


def test_factory_requires_api_key_for_anthropic(tmp_path: Path) -> None:
    settings = Settings(backend="anthropic", cache_dir=tmp_path, ANTHROPIC_API_KEY=None)
    with pytest.raises(BackendError, match="ANTHROPIC_API_KEY"):
        build_backend(settings)


def test_factory_builds_replay_without_network(tmp_path: Path) -> None:
    backend = build_backend(Settings(backend="replay", model="m", cache_dir=tmp_path))
    assert isinstance(backend, CachingBackend)
    assert backend.model == "m"


# --- ollama backend --------------------------------------------------------------------------


@respx.mock
async def test_ollama_request_shape_and_usage() -> None:
    route = respx.post("http://ollama.test/api/chat").respond(
        json={
            "message": {"content": '{"a": 1}'},
            "done_reason": "stop",
            "prompt_eval_count": 900,
            "eval_count": 70,
        }
    )
    backend = OllamaBackend("qwen3.5", host="http://ollama.test")
    completion = await backend.complete(REQUEST)

    sent = json.loads(route.calls[0].request.content)
    assert sent["think"] is False
    assert sent["options"]["temperature"] == 0
    assert sent["format"] == REQUEST.json_schema
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert completion.text == '{"a": 1}'
    assert completion.usage.input_tokens == 900


@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(500, text="model crashed"), "HTTP 500"),
        (httpx.Response(200, json={"done_reason": "length", "message": {}}), "num_predict"),
        (httpx.Response(200, json={"done_reason": "stop"}), "no message"),
        (httpx.Response(200, text="not json"), "failed"),
    ],
)
async def test_ollama_failures_become_backend_errors(
    response: httpx.Response, message: str
) -> None:
    respx.post("http://ollama.test/api/chat").mock(return_value=response)
    with pytest.raises(BackendError, match=message):
        await OllamaBackend("m", host="http://ollama.test").complete(REQUEST)


@respx.mock
async def test_ollama_connection_error() -> None:
    respx.post("http://ollama.test/api/chat").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(BackendError, match="failed"):
        await OllamaBackend("m", host="http://ollama.test").complete(REQUEST)


# --- openai-compatible (MiniMax) backend -----------------------------------------------------


def _minimax(**extra: object) -> OpenAICompatibleBackend:
    return OpenAICompatibleBackend(
        "MiniMax-M3",
        base_url="http://minimax.test/v1/",
        api_key="test-key",
        extra_body={"thinking": {"type": "disabled"}, "reasoning_split": True, **extra},
    )


@respx.mock
async def test_openai_compatible_request_shape_and_usage() -> None:
    route = respx.post("http://minimax.test/v1/chat/completions").respond(
        json={
            "choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1500,
                "completion_tokens": 60,
                "prompt_tokens_details": {"cached_tokens": 1200},
            },
        }
    )
    completion = await _minimax().complete(REQUEST)

    sent = route.calls[0].request
    body = json.loads(sent.content)
    assert sent.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "MiniMax-M3"
    assert body["thinking"] == {"type": "disabled"}
    assert body["reasoning_split"] is True
    assert body["max_completion_tokens"] == REQUEST.max_tokens
    assert "temperature" not in body  # unset: the provider's default
    assert completion.text == '{"a": 1}'
    assert completion.usage.cache_read_input_tokens == 1200
    assert completion.usage.input_tokens == 300  # 1500 prompt tokens minus 1200 cached


@respx.mock
async def test_openai_compatible_sends_a_set_temperature() -> None:
    route = respx.post("http://minimax.test/v1/chat/completions").respond(
        json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    )
    await _minimax().complete(REQUEST.model_copy(update={"temperature": 0.0}))
    assert json.loads(route.calls[0].request.content)["temperature"] == 0.0


@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, text="invalid api key"), "HTTP 401"),
        (httpx.Response(200, json={"choices": []}), "no message"),
        (
            httpx.Response(
                200, json={"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
            ),
            "max tokens",
        ),
        (
            httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
            "empty",
        ),
    ],
)
async def test_openai_compatible_failures(response: httpx.Response, message: str) -> None:
    respx.post("http://minimax.test/v1/chat/completions").mock(return_value=response)
    with pytest.raises(BackendError, match=message):
        await _minimax().complete(REQUEST)


OK_BODY = {"choices": [{"message": {"content": '{"a": 1}'}, "finish_reason": "stop"}]}
RATE_LIMITED = httpx.Response(429, json={"error": {"type": "rate_limit_error"}})


@respx.mock
async def test_openai_compatible_waits_out_a_rate_limit() -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    respx.post("http://minimax.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}),
            RATE_LIMITED,
            httpx.Response(200, json=OK_BODY),
        ]
    )
    backend = OpenAICompatibleBackend(
        "MiniMax-M3", base_url="http://minimax.test/v1", api_key="k", sleep=sleep
    )
    completion = await backend.complete(REQUEST)
    assert completion.text == '{"a": 1}'
    assert waits == [3.0, 4.0]  # the server's Retry-After, then exponential backoff


@respx.mock
async def test_openai_compatible_gives_up_after_its_retries() -> None:
    async def sleep(_: float) -> None:
        return None

    route = respx.post("http://minimax.test/v1/chat/completions").mock(return_value=RATE_LIMITED)
    backend = OpenAICompatibleBackend(
        "MiniMax-M3",
        base_url="http://minimax.test/v1",
        api_key="k",
        sleep=sleep,
        max_rate_limit_retries=2,
    )
    with pytest.raises(BackendError, match="HTTP 429"):
        await backend.complete(REQUEST)
    assert route.call_count == 3


def test_factory_builds_minimax(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "k")
    backend = build_backend(Settings(backend="minimax", model="MiniMax-M3", cache_dir=tmp_path))
    assert isinstance(backend, CachingBackend)
    assert backend.model == "MiniMax-M3"


def test_factory_requires_minimax_key(tmp_path: Path) -> None:
    with pytest.raises(BackendError, match="MINIMAX_API_KEY"):
        build_backend(Settings(backend="minimax", cache_dir=tmp_path))


@respx.mock
async def test_ollama_non_object_body() -> None:
    respx.post("http://ollama.test/api/chat").respond(json=[1, 2])
    with pytest.raises(BackendError, match="not a JSON object"):
        await OllamaBackend("m", host="http://ollama.test").complete(REQUEST)


@pytest.mark.parametrize(
    ("backend", "key_var", "base_url"),
    [
        ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
        ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1"),
    ],
)
def test_factory_builds_any_openai_compatible_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, key_var: str, base_url: str
) -> None:
    monkeypatch.setenv(key_var, "k")
    built = build_backend(
        Settings(backend=backend, model="some/model", cache_dir=tmp_path), use_cache=False
    )
    assert isinstance(built, OpenAICompatibleBackend)
    assert built.model == "some/model"
    assert str(built._client.base_url).rstrip("/") == base_url


def test_generic_provider_takes_extra_request_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("PAC_OPENAI_BASE_URL", "http://vllm.test/v1")
    monkeypatch.setenv("PAC_OPENAI_EXTRA_BODY", '{"reasoning_effort": "low"}')
    built = build_backend(Settings(backend="openai", cache_dir=tmp_path), use_cache=False)
    assert isinstance(built, OpenAICompatibleBackend)
    assert built._extra_body == {"reasoning_effort": "low"}
    with pytest.raises(BackendError, match="OPENROUTER_API_KEY"):
        build_backend(Settings(backend="openrouter", cache_dir=tmp_path))


@respx.mock
async def test_claude_through_openrouter_marks_the_system_prompt_for_caching() -> None:
    reply = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    route = respx.post("http://openrouter.test/v1/chat/completions").respond(json=reply)
    for model in ("anthropic/claude-haiku-5.5", "openai/gpt-6-luna"):
        backend = OpenAICompatibleBackend(
            model, base_url="http://openrouter.test/v1", api_key="test-key"
        )
        await backend.complete(REQUEST)
    claude, other = (json.loads(c.request.content)["messages"][0] for c in route.calls)
    assert claude["content"] == [
        {"type": "text", "text": REQUEST.system, "cache_control": {"type": "ephemeral"}}
    ]
    assert other["content"] == REQUEST.system
