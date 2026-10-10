from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import httpx2
import pytest
import respx

from pac.backends import BackendError, CacheMiss, CachingBackend, LLMRequest, build_backend
from pac.backends.anthropic_backend import AnthropicBackend
from pac.backends.fake import FakeBackend
from pac.backends.ollama_backend import OllamaBackend
from pac.settings import Settings

REQUEST = LLMRequest(
    system="policy text", user="event", json_schema={"type": "object"}, max_tokens=64
)


# --- cache key -------------------------------------------------------------------------------


def test_cache_key_is_stable_and_sensitive() -> None:
    assert REQUEST.cache_key("m") == REQUEST.cache_key("m")
    assert REQUEST.cache_key("m") != REQUEST.cache_key("other")
    assert REQUEST.cache_key("m") != REQUEST.model_copy(update={"system": "x"}).cache_key("m")


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
