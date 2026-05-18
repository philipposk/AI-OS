"""Phase V tests: cost budgets + circuit breaker. No network."""
from __future__ import annotations

import time

import pytest
import requests

from router import circuit as cb_mod
from router.base import ChatResult, ProviderUnavailable


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "bc.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.delenv("WORKFLOW_BUDGET_USD", raising=False)
    monkeypatch.delenv("GLOBAL_BUDGET_USD", raising=False)
    cb_mod.reset_for_tests()


# ---------- budget ----------


def test_budget_unlimited_when_unset(monkeypatch):
    from orchestrator.budget import assert_within
    assert_within({"workflow_id": "wf-1"})  # no raise


def test_budget_global_blocks_when_exceeded(monkeypatch):
    from orchestrator.budget import assert_within, BudgetExceeded
    from storage.accounting import record
    monkeypatch.setenv("GLOBAL_BUDGET_USD", "0.001")
    record(provider="anthropic", model="claude-haiku-4-5",
           prompt_tokens=1000, completion_tokens=500, task_type="plan", workflow_id="wf-1")
    # haiku 1000/500 → ~$0.0035 > $0.001 → should raise
    with pytest.raises(BudgetExceeded) as ei:
        assert_within({"workflow_id": "wf-2"})
    assert ei.value.scope == "global"


def test_budget_workflow_blocks_only_that_workflow(monkeypatch):
    from orchestrator.budget import assert_within, BudgetExceeded
    from storage.accounting import record
    monkeypatch.setenv("WORKFLOW_BUDGET_USD", "0.001")
    record(provider="anthropic", model="claude-haiku-4-5",
           prompt_tokens=1000, completion_tokens=500, task_type="plan", workflow_id="wf-a")

    with pytest.raises(BudgetExceeded) as ei:
        assert_within({"workflow_id": "wf-a"})
    assert ei.value.scope == "workflow"

    # Different workflow id has its own ledger entry → not blocked
    assert_within({"workflow_id": "wf-b"})  # no raise


def test_budget_state_ceiling_overrides_env(monkeypatch):
    from orchestrator.budget import workflow_limit
    monkeypatch.setenv("WORKFLOW_BUDGET_USD", "0.50")
    assert workflow_limit({"budget_ceiling_usd": 2.00}) == 2.00
    assert workflow_limit({}) == 0.50
    assert workflow_limit({"budget_ceiling_usd": 0}) == 0.50  # zero falls back to env


def test_budget_status_snapshot(monkeypatch):
    from orchestrator.budget import status
    from storage.accounting import record
    monkeypatch.setenv("GLOBAL_BUDGET_USD", "10")
    record(provider="anthropic", model="claude-haiku-4-5",
           prompt_tokens=1000, completion_tokens=500, task_type="plan", workflow_id="wf-z")
    snap = status({"workflow_id": "wf-z"})
    assert snap["workflow_id"] == "wf-z"
    assert snap["workflow_cost_usd"] > 0
    assert snap["global_limit_usd"] == 10.0
    assert snap["global_pct"] is not None


# ---------- circuit breaker ----------


def test_breaker_opens_after_threshold():
    bk = cb_mod.CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)
    assert not bk.is_open("groq")
    bk.record_failure("groq", "first")
    assert not bk.is_open("groq")
    bk.record_failure("groq", "second")
    assert bk.is_open("groq")


def test_breaker_hard_failure_trips_immediately():
    bk = cb_mod.CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0)
    bk.record_failure("groq", "401 unauth", hard=True)
    assert bk.is_open("groq")


def test_breaker_closes_after_cooldown():
    bk = cb_mod.CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
    bk.record_failure("groq", "boom")
    assert bk.is_open("groq")
    time.sleep(0.07)
    assert not bk.is_open("groq")


def test_breaker_success_resets():
    bk = cb_mod.CircuitBreaker(failure_threshold=3, cooldown_seconds=10.0)
    bk.record_failure("groq", "a")
    bk.record_failure("groq", "b")
    bk.record_success("groq")
    assert not bk.is_open("groq")
    # Counter was reset, so we should be able to take 2 more before tripping
    bk.record_failure("groq", "c")
    bk.record_failure("groq", "d")
    assert not bk.is_open("groq")


def test_breaker_snapshot_shape():
    bk = cb_mod.CircuitBreaker(failure_threshold=2, cooldown_seconds=10.0)
    bk.record_failure("groq", "x"); bk.record_failure("groq", "y")
    snap = bk.snapshot()
    assert "groq" in snap
    assert snap["groq"]["open"] is True
    assert snap["groq"]["failures"] == 2
    assert snap["groq"]["cooldown_remaining_s"] > 0


# ---------- router integration ----------


class _StubProvider:
    name = "openrouter"
    def __init__(self, *, raise_with=None):
        self.raise_with = raise_with
        self.calls = 0
    def is_available(self): return True
    def default_model(self): return "stub"
    def chat(self, messages, model, max_tokens=1024, temperature=0.7):
        self.calls += 1
        if self.raise_with is not None:
            raise self.raise_with
        return ChatResult(text="ok", model=model, provider=self.name,
                          prompt_tokens=1, completion_tokens=1)
    def chat_stream(self, *a, **k):
        if self.raise_with is not None:
            raise self.raise_with
        yield "ok"
        yield ChatResult(text="ok", model="stub", provider=self.name,
                         prompt_tokens=1, completion_tokens=1)


def test_router_records_success_on_chat():
    from router.router import ModelRouter
    p = _StubProvider()
    r = ModelRouter(providers={"openrouter": p})
    r.chat([{"role": "user", "content": "x"}], task_type="simple", model="stub")
    snap = cb_mod.get_breaker().snapshot()
    # Either no entry (because it never failed) or failures==0
    assert snap.get("openrouter", {"failures": 0})["failures"] == 0


def test_router_records_failure_and_skips_after_threshold(monkeypatch):
    from router.router import ModelRouter
    monkeypatch.setenv("CIRCUIT_FAILURE_THRESHOLD", "2")
    cb_mod.reset_for_tests()

    # rebuild fresh breaker so env var picks up
    bk = cb_mod.get_breaker()
    assert bk.failure_threshold == 2

    err = requests.exceptions.HTTPError("502 bad gateway")
    err.response = None  # type: ignore[attr-defined]
    p = _StubProvider(raise_with=err)
    r = ModelRouter(providers={"openrouter": p})

    for _ in range(2):
        with pytest.raises(Exception):
            r.chat([{"role": "user", "content": "x"}], task_type="simple", model="stub")

    # Now the breaker should be open
    assert bk.is_open("openrouter")
    # resolve() should now refuse since no other providers are configured
    with pytest.raises(ProviderUnavailable):
        r.resolve("simple")


def test_hard_failure_classification():
    from router.router import _is_hard_failure

    # 401 → hard
    class _R:
        status_code = 401
    e401 = requests.exceptions.HTTPError("401")
    e401.response = _R()  # type: ignore[assignment]
    assert _is_hard_failure(e401) is True

    # 429 → not hard
    class _R2:
        status_code = 429
    e429 = requests.exceptions.HTTPError("429")
    e429.response = _R2()  # type: ignore[assignment]
    assert _is_hard_failure(e429) is False

    # ProviderUnavailable → hard
    assert _is_hard_failure(ProviderUnavailable("no key")) is True

    # Random RuntimeError → not hard
    assert _is_hard_failure(RuntimeError("x")) is False
