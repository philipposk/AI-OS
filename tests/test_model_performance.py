"""Per-model performance aggregation from joined ledger + retrospectives."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "perf.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    # .env load at router import sets ROUTER_MODEL_* to user's preferences —
    # scrub to keep test deterministic.
    for tt in ("ANALYZE", "PLAN", "CODE", "REVIEW", "SUMMARIZE", "TEST", "SIMPLE"):
        monkeypatch.delenv(f"ROUTER_MODEL_{tt}", raising=False)
    monkeypatch.delenv("USE_LEARNED_MODELS", raising=False)
    # Reset circuit-breaker singleton so prior test failures don't trip providers here.
    from router import circuit
    circuit.reset_for_tests()
    yield


def _seed_call(workflow_id, provider, model, task_type, cost):
    from storage import accounting
    from datetime import datetime, timezone
    from storage.db import connect
    with connect() as conn:
        conn.executescript(accounting._SCHEMA)
        conn.execute(
            "INSERT INTO ledger (ts, provider, model, task_type, prompt_tokens, "
            "completion_tokens, cost_usd, workflow_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), provider, model, task_type,
             100, 50, cost, workflow_id),
        )


def _seed_retro(workflow_id, verdict, retries=0):
    from storage import retrospectives as r
    r.record(workflow_id=workflow_id, task="t", verdict=verdict,
             plan_steps=1, test_retries=retries, tests_passed=(verdict == "success"),
             files_touched=1, cost_usd=0.0, crew_mode=False,
             commit_sha="x" if verdict == "success" else None)


def test_report_empty_when_no_data():
    from storage import model_performance as mp
    assert mp.report() == []


def test_report_joins_ledger_and_retrospectives():
    # Two workflows on haiku: one success, one fail.
    _seed_call("wf-1", "anthropic", "claude-haiku-4-5", "plan", 0.005)
    _seed_retro("wf-1", "success")
    _seed_call("wf-2", "anthropic", "claude-haiku-4-5", "plan", 0.005)
    _seed_retro("wf-2", "tests_failed", retries=3)

    from storage import model_performance as mp
    rows = mp.report()
    assert len(rows) == 1
    r = rows[0]
    assert r.provider == "anthropic"
    assert r.model == "claude-haiku-4-5"
    assert r.task_type == "plan"
    assert r.n_workflows == 2
    assert r.n_successes == 1
    assert r.success_rate == 0.5
    assert r.avg_retries == pytest.approx(1.5)


def test_report_filters_by_task_type():
    _seed_call("wf-a", "groq", "llama-3.3-70b", "plan", 0.0)
    _seed_retro("wf-a", "success")
    _seed_call("wf-a", "groq", "llama-3.3-70b", "analyze", 0.0)
    # Same workflow uses model for two task_types; retrospective covers the whole workflow.

    from storage import model_performance as mp
    plan_rows = mp.report(task_type="plan")
    analyze_rows = mp.report(task_type="analyze")
    assert len(plan_rows) == 1 and plan_rows[0].task_type == "plan"
    assert len(analyze_rows) == 1 and analyze_rows[0].task_type == "analyze"


def test_report_ignores_workflows_without_retrospectives():
    """A call recorded mid-workflow before the retrospective runs shouldn't
    pollute the stats."""
    _seed_call("wf-orphan", "anthropic", "claude-haiku-4-5", "plan", 0.005)
    # NO retrospective for wf-orphan
    from storage import model_performance as mp
    assert mp.report() == []


def test_best_model_for_picks_highest_success_rate():
    # Model A: 5/5 success
    for i in range(5):
        wf = f"wf-a-{i}"
        _seed_call(wf, "anthropic", "claude-haiku-4-5", "plan", 0.005)
        _seed_retro(wf, "success")
    # Model B: 2/5 success
    for i in range(5):
        wf = f"wf-b-{i}"
        _seed_call(wf, "groq", "llama-3.3-70b", "plan", 0.0)
        _seed_retro(wf, "success" if i < 2 else "tests_failed", retries=0 if i < 2 else 3)

    from storage import model_performance as mp
    best = mp.best_model_for("plan", min_workflows=5)
    assert best == ("anthropic", "claude-haiku-4-5")


def test_best_model_for_respects_min_workflows():
    # Only 3 workflows on a perfect-but-noisy model
    for i in range(3):
        wf = f"wf-{i}"
        _seed_call(wf, "anthropic", "claude-haiku-4-5", "plan", 0.005)
        _seed_retro(wf, "success")

    from storage import model_performance as mp
    assert mp.best_model_for("plan", min_workflows=5) is None
    assert mp.best_model_for("plan", min_workflows=3) == ("anthropic", "claude-haiku-4-5")


def test_best_model_for_max_cost_filter():
    # Expensive model with perfect record
    for i in range(5):
        wf = f"wf-x-{i}"
        _seed_call(wf, "anthropic", "claude-opus-4-7", "plan", 1.00)
        _seed_retro(wf, "success")
    # Cheap model with also-good record
    for i in range(5):
        wf = f"wf-y-{i}"
        _seed_call(wf, "groq", "llama-3.3-70b", "plan", 0.0)
        _seed_retro(wf, "success")

    from storage import model_performance as mp
    # With no cost filter, both tied on success rate; tie-break is n_workflows then cost asc → cheap wins
    best = mp.best_model_for("plan", min_workflows=5)
    assert best == ("groq", "llama-3.3-70b")
    # Cap at $0.01/call → opus eliminated, groq survives
    cheap = mp.best_model_for("plan", min_workflows=5, max_cost_per_call=0.01)
    assert cheap == ("groq", "llama-3.3-70b")


def test_router_uses_learned_models_when_flag_on(monkeypatch):
    """USE_LEARNED_MODELS=1 + retrospective data → router.resolve picks the winner."""
    # Seed: groq wins for plan with 5/5 success
    for i in range(5):
        wf = f"wf-{i}"
        _seed_call(wf, "groq", "llama-3.3-70b", "plan", 0.0)
        _seed_retro(wf, "success")

    monkeypatch.setenv("USE_LEARNED_MODELS", "1")

    from router.base import BaseProvider, ChatResult

    class _Stub(BaseProvider):
        def __init__(self, name): self.name = name
        def is_available(self): return True
        def default_model(self): return "default-" + self.name
        def chat(self, *a, **k):
            return ChatResult(text="", model="", provider=self.name)

    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic"),
        "groq": _Stub("groq"),
    })
    prov, model = r.resolve("plan")
    assert prov == "groq"
    assert model == "llama-3.3-70b"


def test_router_ignores_learned_models_when_flag_off(monkeypatch):
    """Default behavior unchanged when flag is off."""
    for i in range(5):
        wf = f"wf-{i}"
        _seed_call(wf, "groq", "llama-3.3-70b", "plan", 0.0)
        _seed_retro(wf, "success")
    monkeypatch.delenv("USE_LEARNED_MODELS", raising=False)

    from router.base import BaseProvider, ChatResult

    class _Stub(BaseProvider):
        def __init__(self, name): self.name = name
        def is_available(self): return True
        def default_model(self): return "default-" + self.name
        def chat(self, *a, **k):
            return ChatResult(text="", model="", provider=self.name)

    from router.router import ModelRouter
    r = ModelRouter(providers={
        "anthropic": _Stub("anthropic"),
        "groq": _Stub("groq"),
    })
    # Default behavior: claude-haiku-4-5 hint → anthropic wins
    prov, model = r.resolve("plan")
    assert prov == "anthropic"
    assert model == "claude-haiku-4-5"


def test_summary_groups_by_task_type():
    _seed_call("wf-1", "anthropic", "claude-haiku-4-5", "plan", 0.005)
    _seed_retro("wf-1", "success")
    _seed_call("wf-2", "groq", "llama-3.3-70b", "analyze", 0.0)
    _seed_retro("wf-2", "success")

    from storage import model_performance as mp
    s = mp.summary()
    assert "plan" in s and "analyze" in s
    assert s["plan"]["best"] == ("anthropic", "claude-haiku-4-5")
    assert s["analyze"]["best"] == ("groq", "llama-3.3-70b")
