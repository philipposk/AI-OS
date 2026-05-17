from __future__ import annotations

import os
from pathlib import Path

import pytest

from router import ModelRouter, ProviderUnavailable
from router.base import BaseProvider, ChatResult
from router.costs import estimate_cost_usd
from router.router import _parse_model_id


class FakeProvider(BaseProvider):
    def __init__(self, name: str, available: bool = True, default: str = "fake-model"):
        self.name = name
        self._available = available
        self._default = default
        self.calls: list[tuple] = []

    def is_available(self) -> bool:
        return self._available

    def default_model(self) -> str:
        return self._default

    def chat(self, messages, model, max_tokens=1024, temperature=0.7):
        self.calls.append((tuple((m["role"], m["content"]) for m in messages), model, max_tokens, temperature))
        return ChatResult(
            text=f"reply from {self.name}",
            model=model,
            provider=self.name,
            prompt_tokens=10,
            completion_tokens=20,
        )


def _router(**providers) -> ModelRouter:
    return ModelRouter(providers=providers)


def test_parse_model_id_prefixed():
    assert _parse_model_id("openrouter:meta-llama/llama-3.2-3b-instruct:free") == (
        "openrouter",
        "meta-llama/llama-3.2-3b-instruct:free",
    )


def test_parse_model_id_bare_anthropic():
    assert _parse_model_id("claude-haiku-4-5") == ("anthropic", "claude-haiku-4-5")


def test_parse_model_id_unknown_bare():
    assert _parse_model_id("some-random-model") == (None, "some-random-model")


def test_resolve_uses_hinted_provider_when_available():
    router = _router(
        anthropic=FakeProvider("anthropic", available=True),
        openrouter=FakeProvider("openrouter", available=True),
    )
    prov, model = router.resolve("plan", override_model="claude-haiku-4-5")
    assert prov == "anthropic"
    assert model == "claude-haiku-4-5"


def test_resolve_falls_back_when_hinted_unavailable():
    router = _router(
        anthropic=FakeProvider("anthropic", available=False),
        openrouter=FakeProvider("openrouter", available=True, default="meta-llama/llama-3.2-3b-instruct:free"),
    )
    prov, model = router.resolve("plan", override_model="claude-haiku-4-5")
    assert prov == "openrouter"
    # Falls back to that provider's default rather than passing an anthropic id to openrouter.
    assert model == "meta-llama/llama-3.2-3b-instruct:free"


def test_resolve_no_provider_available_raises():
    router = _router(
        anthropic=FakeProvider("anthropic", available=False),
        openrouter=FakeProvider("openrouter", available=False),
    )
    with pytest.raises(ProviderUnavailable):
        router.resolve("plan")


def test_chat_records_to_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "ledger.sqlite"))
    fp = FakeProvider("anthropic", available=True, default="claude-haiku-4-5")
    router = _router(anthropic=fp)
    res = router.chat(
        [{"role": "user", "content": "hi"}],
        task_type="plan",
        model="claude-haiku-4-5",
        max_tokens=16,
        temperature=0.1,
    )
    assert res.text == "reply from anthropic"
    assert fp.calls and fp.calls[0][1] == "claude-haiku-4-5"
    from storage.accounting import iter_entries

    entries = list(iter_entries())
    assert len(entries) == 1
    e = entries[0]
    assert e.provider == "anthropic"
    assert e.model == "claude-haiku-4-5"
    assert e.prompt_tokens == 10
    assert e.completion_tokens == 20
    assert e.task_type == "plan"
    # Haiku pricing: 10 in @ $1/MTok + 20 out @ $5/MTok
    assert e.cost_usd == pytest.approx((10 / 1e6) * 1.0 + (20 / 1e6) * 5.0)


def test_cost_estimate_free_model_is_zero():
    assert estimate_cost_usd("meta-llama/llama-3.2-3b-instruct:free", 5000, 5000) == 0.0


def test_cost_estimate_unknown_model_is_zero_not_crash():
    assert estimate_cost_usd("brand-new-model-id", 1000, 1000) == 0.0
