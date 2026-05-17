from __future__ import annotations

import os
import time

import pytest


@pytest.fixture(autouse=True)
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "test.sqlite"))
    yield


# ---------- queue ----------


def test_queue_push_pop_priority_order():
    from storage import queue as q

    q.push("low", priority=0)
    high = q.push("high", priority=10)
    mid = q.push("mid", priority=5)

    first = q.pop()
    second = q.pop()
    third = q.pop()
    fourth = q.pop()

    assert first.id == high.id
    assert second.id == mid.id
    assert third.task == "low"
    assert fourth is None


def test_queue_pop_marks_in_progress():
    from storage import queue as q

    q.push("t")
    t = q.pop()
    assert t.status == "in_progress"
    assert t.started_at is not None
    pending = q.size("pending")
    in_prog = q.size("in_progress")
    assert pending == 0 and in_prog == 1


def test_queue_lifecycle_done_failed_cancel():
    from storage import queue as q

    a = q.push("a")
    b = q.push("b")
    c = q.push("c")
    ta = q.pop()
    q.mark_done(ta.id)
    tb = q.pop()
    q.mark_failed(tb.id, "boom")
    q.cancel(c.id)

    counts = q.status_counts()
    assert counts.get("done") == 1
    assert counts.get("failed") == 1
    assert counts.get("cancelled") == 1
    assert counts.get("pending", 0) == 0


# ---------- memory ----------


def test_memory_fts_search_matches_substring_tokens():
    from storage import memory as mem

    mem.add("Switched orchestrator to LangGraph state machine", kind="review", workflow_id="wf1")
    mem.add("Added a --version flag to cli.py for release tagging", kind="review", workflow_id="wf2")
    mem.add("Bumped dependency: numpy 2.0", kind="note")

    hits = mem.search("langgraph orchestrator")
    assert hits and hits[0].workflow_id == "wf1"

    hits2 = mem.search("version flag cli")
    assert hits2 and hits2[0].workflow_id == "wf2"


def test_memory_kind_filter_and_for_workflow():
    from storage import memory as mem

    mem.add("alpha analysis note", kind="analysis", workflow_id="wfX")
    mem.add("beta review note", kind="review", workflow_id="wfX")
    mem.add("gamma review note", kind="review", workflow_id="wfY")

    only_review = mem.search("review note", kind="review")
    assert {h.workflow_id for h in only_review} == {"wfX", "wfY"}

    docs = mem.for_workflow("wfX")
    assert {d.text for d in docs} == {"alpha analysis note", "beta review note"}


def test_memory_empty_query_returns_nothing():
    from storage import memory as mem
    assert mem.search("") == []
    assert mem.search("   ") == []


# ---------- accounting (SQLite) ----------


def test_accounting_records_and_reports():
    from storage import accounting as acc

    acc.record(provider="anthropic", model="claude-haiku-4-5", prompt_tokens=1000, completion_tokens=500, task_type="plan", workflow_id="w1")
    acc.record(provider="openrouter", model="meta-llama/llama-3.2-3b-instruct:free", prompt_tokens=2000, completion_tokens=1000, task_type="code", workflow_id="w1")
    acc.record(provider="anthropic", model="claude-haiku-4-5", prompt_tokens=200, completion_tokens=100, task_type="review", workflow_id="w2")

    rep = acc.report()
    assert rep["total_calls"] == 3
    assert rep["by_provider"]["anthropic"]["calls"] == 2
    assert rep["by_provider"]["openrouter"]["cost_usd"] == 0.0  # free tier
    # Haiku cost: (1000+200) in @ $1/MTok + (500+100) out @ $5/MTok = 0.0012 + 0.003 = 0.0042
    assert rep["by_provider"]["anthropic"]["cost_usd"] == pytest.approx(0.0042, abs=1e-9)

    only_w1 = acc.report(workflow_id="w1")
    assert only_w1["total_calls"] == 2
