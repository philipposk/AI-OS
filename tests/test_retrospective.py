"""Phase Z: retrospective node + storage."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "retro.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.delenv("CREW_MODE", raising=False)
    monkeypatch.delenv("WORKFLOW_BUDGET_USD", raising=False)
    monkeypatch.delenv("GLOBAL_BUDGET_USD", raising=False)


def test_retrospective_record_and_aggregate():
    from storage import retrospectives as r

    r.record(workflow_id="wf-1", task="add x", verdict="success",
             plan_steps=2, test_retries=0, tests_passed=True,
             files_touched=1, cost_usd=0.012, crew_mode=False,
             commit_sha="abc123", notes="ok")
    r.record(workflow_id="wf-2", task="add y", verdict="tests_failed",
             plan_steps=3, test_retries=3, tests_passed=False,
             files_touched=2, cost_usd=0.03, crew_mode=True, notes="flaky")
    r.record(workflow_id="wf-3", task="z", verdict="rejected_commit",
             plan_steps=1, test_retries=0, tests_passed=True,
             files_touched=1, cost_usd=0.001, crew_mode=False)

    entries = list(r.iter_entries())
    assert len(entries) == 3
    assert entries[0].verdict == "success"
    assert entries[0].commit_sha == "abc123"
    assert entries[1].crew_mode is True
    assert entries[2].tests_passed is True

    agg = r.aggregate()
    assert agg["total"] == 3
    assert agg["by_verdict"]["success"]["count"] == 1
    assert agg["by_verdict"]["tests_failed"]["avg_retries"] == 3.0


def test_retrospective_node_verdict_success(monkeypatch):
    from orchestrator import nodes

    monkeypatch.setattr(nodes, "_chat", lambda *a, **kw: "no improvement")
    out = nodes.retrospective_node({
        "workflow_id": "wf-s", "task": "t",
        "plan": [{"title": "a"}, {"title": "b"}],
        "code_changes": [{"path": "x.py"}, {"path": "y.py"}],
        "test_results": {"passed": True},
        "commit_sha": "deadbeef",
        "retries": 0,
    })
    assert out["retrospective"]["verdict"] == "success"
    assert out["retrospective"]["notes"] == "no improvement"

    from storage import retrospectives as r
    rows = list(r.iter_entries("wf-s"))
    assert len(rows) == 1
    assert rows[0].verdict == "success"
    assert rows[0].files_touched == 2


def test_retrospective_node_verdict_tests_failed(monkeypatch):
    from orchestrator import nodes

    monkeypatch.setattr(nodes, "_chat", lambda *a, **kw: "add edge tests")
    out = nodes.retrospective_node({
        "workflow_id": "wf-f", "task": "t",
        "plan": [{"title": "a"}],
        "code_changes": [{"path": "x.py"}],
        "test_results": {"passed": False},
        "commit_sha": None,
        "retries": 3,
    })
    assert out["retrospective"]["verdict"] == "tests_failed"


def test_retrospective_node_verdict_budget_abort(monkeypatch):
    from orchestrator import nodes

    monkeypatch.setattr(nodes, "_chat", lambda *a, **kw: "")
    out = nodes.retrospective_node({
        "workflow_id": "wf-b", "task": "t",
        "plan": [],
        "code_changes": [],
        "budget_blocked": {"scope": "workflow", "next": "do_plan"},
    })
    assert out["retrospective"]["verdict"] == "budget_abort"


def test_retrospective_node_swallows_critique_errors(monkeypatch):
    """LLM hiccup during critique must not kill the node."""
    from orchestrator import nodes

    def boom(*a, **kw):
        raise RuntimeError("provider down")

    monkeypatch.setattr(nodes, "_chat", boom)
    out = nodes.retrospective_node({
        "workflow_id": "wf-e", "task": "t",
        "plan": [], "code_changes": [], "commit_sha": "abc",
        "test_results": {"passed": True},
    })
    # Verdict still recorded, notes empty.
    assert out["retrospective"]["verdict"] == "success"
    assert out["retrospective"]["notes"] == ""
