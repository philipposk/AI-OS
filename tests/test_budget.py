"""Phase U: cost budget + circuit breaker."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "test.sqlite"))
    monkeypatch.delenv("WORKFLOW_BUDGET_USD", raising=False)
    monkeypatch.delenv("GLOBAL_BUDGET_USD", raising=False)
    yield


def _record(workflow_id: str, cost_usd: float, *, model: str = "claude-haiku-4-5"):
    """Force a ledger row with a known cost — bypasses estimate_cost_usd()."""
    from datetime import datetime, timezone
    from storage import accounting
    from storage.db import connect

    with connect() as conn:
        conn.executescript(accounting._SCHEMA)
        conn.execute(
            "INSERT INTO ledger (ts, provider, model, task_type, prompt_tokens, completion_tokens, cost_usd, workflow_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), "anthropic", model, "test", 0, 0, cost_usd, workflow_id),
        )


def test_workflow_cost_sums_workflow_only():
    from storage import accounting

    _record("wf-a", 0.10)
    _record("wf-a", 0.25)
    _record("wf-b", 1.00)
    assert accounting.workflow_cost("wf-a") == pytest.approx(0.35)
    assert accounting.workflow_cost("wf-b") == pytest.approx(1.00)
    assert accounting.workflow_cost("wf-missing") == 0.0


def test_total_cost_sums_all():
    from storage import accounting

    _record("wf-a", 0.10)
    _record("wf-b", 1.00)
    assert accounting.total_cost() == pytest.approx(1.10)


def test_assert_within_no_limits_is_quiet():
    from orchestrator import budget

    _record("wf-1", 999.0)  # huge spend, but no limit set
    # No exception.
    budget.assert_within({"workflow_id": "wf-1"})


def test_assert_within_workflow_env_trips(monkeypatch):
    from orchestrator import budget

    monkeypatch.setenv("WORKFLOW_BUDGET_USD", "0.50")
    _record("wf-1", 0.49)
    budget.assert_within({"workflow_id": "wf-1"})  # under
    _record("wf-1", 0.02)                          # now 0.51 ≥ 0.50
    with pytest.raises(budget.BudgetExceeded) as ei:
        budget.assert_within({"workflow_id": "wf-1"})
    assert ei.value.scope == "workflow"


def test_state_ceiling_overrides_env(monkeypatch):
    from orchestrator import budget

    monkeypatch.setenv("WORKFLOW_BUDGET_USD", "0.10")
    _record("wf-1", 0.20)
    # env says 0.10 → over. State ceiling 1.00 → under.
    budget.assert_within({"workflow_id": "wf-1", "budget_ceiling_usd": 1.00})


def test_global_limit_trips_independently(monkeypatch):
    from orchestrator import budget

    monkeypatch.setenv("GLOBAL_BUDGET_USD", "1.00")
    _record("wf-a", 0.60)
    _record("wf-b", 0.50)  # total 1.10
    with pytest.raises(budget.BudgetExceeded) as ei:
        budget.assert_within({"workflow_id": "wf-z"})
    assert ei.value.scope == "global"


def test_status_reports_percent(monkeypatch):
    from orchestrator import budget

    monkeypatch.setenv("WORKFLOW_BUDGET_USD", "1.00")
    monkeypatch.setenv("GLOBAL_BUDGET_USD", "10.00")
    _record("wf-1", 0.50)
    out = budget.status({"workflow_id": "wf-1"})
    assert out["workflow_pct"] == pytest.approx(50.0)
    assert out["global_pct"] == pytest.approx(5.0)


def test_budget_blocked_routes_to_checkpoint(monkeypatch):
    """End-to-end: when analyze_node trips the budget, do_analyze returns
    {"budget_blocked": ...}; the graph's conditional edge picks checkpoint_budget.
    """
    from orchestrator.graph import _budget_or, _after_budget

    state_clean = {"approved": True}
    state_blocked = {"budget_blocked": {"scope": "workflow", "next": "do_plan",
                                        "current": 0.6, "limit": 0.5}}

    route = _budget_or("do_plan")
    assert route(state_clean) == "do_plan"
    assert route(state_blocked) == "checkpoint_budget"

    # After checkpoint: approved=True → resume to budget_resume_node (set by
    # review_budget_checkpoint before it clears budget_blocked).
    assert _after_budget({"approved": True,
                          "budget_resume_node": "do_plan"}) == "do_plan"
    # Abort: approved=False → END
    from langgraph.graph import END
    assert _after_budget({"approved": False}) == END
