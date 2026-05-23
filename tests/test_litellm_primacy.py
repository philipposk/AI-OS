"""LITELLM_PRIMARY=1 makes litellm the default provider for all task_types
that lack an explicit ROUTER_MODEL_<TASK> override. No network."""
from __future__ import annotations

import sys
import types

import pytest

from router.base import BaseProvider, ChatResult


class _Stub(BaseProvider):
    def __init__(self, name, model="m"):
        self.name = name
        self._model = model
        self.calls = 0
    def is_available(self): return True
    def default_model(self): return self._model
    def chat(self, *a, **k):
        self.calls += 1
        return ChatResult(text="", model=self._model, provider=self.name)


@pytest.fixture(autouse=True)
def scrub_env(monkeypatch):
    """Strip user .env overrides so behavior is deterministic."""
    for tt in ("ANALYZE", "PLAN", "CODE", "REVIEW", "SUMMARIZE", "TEST", "SIMPLE"):
        monkeypatch.delenv(f"ROUTER_MODEL_{tt}", raising=False)
        monkeypatch.delenv(f"LITELLM_TASK_MODEL_{tt}", raising=False)
    monkeypatch.delenv("LITELLM_PRIMARY", raising=False)
    monkeypatch.delenv("LITELLM_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("USE_LEARNED_MODELS", raising=False)
    from router import circuit
    circuit.reset_for_tests()


def test_primacy_off_uses_legacy_path(monkeypatch):
    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic", "claude-haiku-4-5"),
        "litellm": _Stub("litellm", "openai/gpt-4o-mini"),
    })
    prov, model = r.resolve("plan")
    assert prov == "anthropic"
    assert model == "claude-haiku-4-5"


def test_primacy_on_picks_litellm(monkeypatch):
    monkeypatch.setenv("LITELLM_PRIMARY", "1")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "anthropic/claude-haiku-4-5")
    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic", "claude-haiku-4-5"),
        "litellm": _Stub("litellm"),
    })
    prov, model = r.resolve("plan")
    assert prov == "litellm"
    assert model == "anthropic/claude-haiku-4-5"


def test_per_task_litellm_model_env_beats_default(monkeypatch):
    monkeypatch.setenv("LITELLM_PRIMARY", "1")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("LITELLM_TASK_MODEL_CODE", "anthropic/claude-sonnet-4-6")
    from router.router import ModelRouter
    r = ModelRouter(providers={"litellm": _Stub("litellm"), "anthropic": _Stub("anthropic")})
    prov, model = r.resolve("code")
    assert prov == "litellm"
    assert model == "anthropic/claude-sonnet-4-6"
    prov, model = r.resolve("plan")
    assert model == "openai/gpt-4o-mini"


def test_router_model_env_pins_task_to_dedicated_provider(monkeypatch):
    """LITELLM_PRIMARY=1 + ROUTER_MODEL_PLAN=claude-haiku-4-5
    → plan goes through anthropic, other tasks through litellm."""
    monkeypatch.setenv("LITELLM_PRIMARY", "1")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("ROUTER_MODEL_PLAN", "claude-haiku-4-5")

    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic", "claude-haiku-4-5"),
        "litellm": _Stub("litellm"),
    })
    prov, model = r.resolve("plan")
    assert prov == "anthropic"
    assert model == "claude-haiku-4-5"

    prov, model = r.resolve("analyze")
    assert prov == "litellm"
    assert model == "openai/gpt-4o-mini"


def test_primacy_falls_back_when_litellm_unavailable(monkeypatch):
    monkeypatch.setenv("LITELLM_PRIMARY", "1")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "openai/gpt-4o-mini")

    class _Unavail(_Stub):
        def is_available(self): return False

    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic", "claude-haiku-4-5"),
        "litellm": _Unavail("litellm"),
    })
    prov, model = r.resolve("plan")
    assert prov == "anthropic"


def test_primacy_falls_back_when_circuit_breaker_open(monkeypatch):
    monkeypatch.setenv("LITELLM_PRIMARY", "1")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "openai/gpt-4o-mini")
    from router import circuit
    bk = circuit.get_breaker()
    bk.record_failure("litellm", "boom", hard=True)
    assert bk.is_open("litellm")

    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic", "claude-haiku-4-5"),
        "litellm": _Stub("litellm"),
    })
    prov, model = r.resolve("plan")
    assert prov == "anthropic"
    circuit.reset_for_tests()


def test_explicit_override_model_beats_primacy(monkeypatch):
    """Caller-supplied override_model wins over LITELLM_PRIMARY."""
    monkeypatch.setenv("LITELLM_PRIMARY", "1")
    monkeypatch.setenv("LITELLM_DEFAULT_MODEL", "openai/gpt-4o-mini")
    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic", "claude-haiku-4-5"),
        "litellm": _Stub("litellm"),
    })
    prov, model = r.resolve("plan", override_model="claude-haiku-4-5")
    assert prov == "anthropic"
    assert model == "claude-haiku-4-5"
