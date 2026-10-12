"""The ledger enforces caps across processes; OpenRouter caches Claude and reports cost."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from pac.guardrail.application.ports import Completion, LLMRequest
from pac.guardrail.domain.classification import Usage
from pac.synthesis.infrastructure.ledger import BudgetExceeded, Cap, Ledger, LedgeredBackend, Price
from pac.synthesis.infrastructure.openrouter import OpenRouterBackend
from tests.support.fakes import FakeBackend

REQUEST = LLMRequest(system="s", user="u", json_schema={}, max_tokens=10)


async def test_calls_are_counted_by_phase_and_capped(tmp_path: Path) -> None:
    ledger = Ledger.load(tmp_path / "ledger.json", {"minimax": Cap(calls=2)})
    backend = LedgeredBackend(FakeBackend(lambda _: "ok", model="m"), ledger, provider="minimax")
    with ledger.phase("one"):
        await backend.complete(REQUEST)
        await backend.complete(REQUEST)
        with pytest.raises(BudgetExceeded):
            await backend.complete(REQUEST)
    data = json.loads((tmp_path / "ledger.json").read_text())
    assert data["totals"]["minimax"]["calls"] == 2
    assert data["rows"][0]["phase"] == "one"


async def test_two_ledgers_on_one_file_add_up(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    a = Ledger.load(path, {})
    b = Ledger.load(path, {})
    for ledger in (a, b, a):
        ledger.record("openrouter", "m", Completion(text="x", model="m"), 0.01)
    calls, usd = Ledger.load(path, {}).spent("openrouter")
    assert calls == 3
    assert usd == pytest.approx(0.03)


async def test_dollar_caps_use_list_prices(tmp_path: Path) -> None:
    ledger = Ledger.load(tmp_path / "l.json", {"anthropic": Cap(usd=0.001)})

    def respond(_: LLMRequest) -> str:
        return "ok"

    inner = FakeBackend(respond, model="claude")
    backend = LedgeredBackend(
        inner, ledger, provider="anthropic", price=Price(input=1.0, output=5.0)
    )
    await backend.complete(LLMRequest(system="x" * 4000, user="u", json_schema={}, max_tokens=10))
    assert ledger.spent("anthropic")[1] == pytest.approx((1000 * 1.0 + 40 * 5.0) / 1e6, rel=0.05)
    assert Price(input=1.0, output=2.0).cost(
        Completion(text="", model="m", usage=Usage(input_tokens=10, output_tokens=5))
    ) == pytest.approx(20 / 1e6)


@respx.mock
async def test_openrouter_caches_the_system_prompt_for_claude_and_reports_cost(
    tmp_path: Path,
) -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 7,
                    "cost": 0.00042,
                    "prompt_tokens_details": {"cached_tokens": 80},
                },
            },
        )
    )
    backend = OpenRouterBackend("anthropic/claude-haiku-5.5", api_key="k")
    ledger = Ledger.load(tmp_path / "l.json", {})
    ledgered = LedgeredBackend(
        backend, ledger, provider="openrouter", price=Price(input=100.0, output=100.0)
    )
    completion = await ledgered.complete(REQUEST)
    body = json.loads(route.calls[0].request.content)
    assert body["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert completion.usage.cache_read_input_tokens == 80
    assert ledger.spent("openrouter")[1] == pytest.approx(0.00042)


@respx.mock
async def test_openrouter_sends_plain_system_text_to_other_models() -> None:
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {},
            },
        )
    )
    await OpenRouterBackend(
        "openai/gpt-6-luna", api_key="k", extra_body={"reasoning": {"effort": "low"}}
    ).complete(REQUEST)
    body = json.loads(route.calls[0].request.content)
    assert body["messages"][0]["content"] == "s"
    assert body["reasoning"] == {"effort": "low"}
