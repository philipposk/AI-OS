"""Phase V: search_node + graph wiring."""
from __future__ import annotations

import pytest


def test_search_node_skips_when_disabled(monkeypatch):
    from orchestrator.nodes import search_node

    called = {"n": 0}

    def fake_search(*a, **k):
        called["n"] += 1
        return [{"title": "x", "url": "y", "snippet": "z"}]

    monkeypatch.setattr("tools.web_search.search", fake_search)
    assert search_node({"task": "anything"}) == {}
    assert called["n"] == 0  # didn't even call the backend


def test_search_node_returns_top_5(monkeypatch):
    from orchestrator.nodes import search_node

    hits = [{"title": f"h{i}", "url": f"u{i}", "snippet": "s"} for i in range(10)]
    monkeypatch.setattr("tools.web_search.search", lambda *a, **k: hits)

    out = search_node({"task": "what is langgraph?", "search_enabled": True})
    assert "search_results" in out
    assert len(out["search_results"]) == 5
    assert out["search_results"][0]["title"] == "h0"


def test_search_node_failure_swallowed(monkeypatch):
    from orchestrator.nodes import search_node

    def boom(*a, **k):
        raise RuntimeError("backend down")

    monkeypatch.setattr("tools.web_search.search", boom)
    out = search_node({"task": "x", "search_enabled": True})
    # Graceful: returns search_results=[] rather than crashing the workflow.
    assert out == {"search_results": []}


def test_plan_node_prepends_search_block(monkeypatch):
    """plan_node should inject 'Recent web context' into the user prompt
    when state.search_results is non-empty.
    """
    captured: dict = {}

    def fake_chat(task_type, system, user, *, workflow_id=None, state=None):
        captured["user"] = user
        return '[{"title": "step", "detail": "d", "files": []}]'

    monkeypatch.setattr("orchestrator.nodes._chat", fake_chat)
    from orchestrator.nodes import plan_node

    state = {
        "task": "Add a --version flag",
        "analysis": "small CLI tweak",
        "search_results": [
            {"title": "argparse docs", "url": "https://docs.python.org/3/library/argparse.html",
             "snippet": "ArgumentParser supports add_argument..."},
            {"title": "PEP 396", "url": "https://peps.python.org/pep-0396/", "snippet": "module __version__"},
        ],
    }
    out = plan_node(state)
    assert "Recent web context" in captured["user"]
    assert "argparse docs" in captured["user"]
    assert "PEP 396" in captured["user"]
    assert out.get("plan")


def test_graph_routes_through_do_search(monkeypatch):
    """build_graph should include the do_search node between analyze and plan."""
    from orchestrator.graph import build_graph

    g = build_graph()
    # CompiledGraph keeps node names accessible via .nodes (LangGraph public attr).
    names = set(g.nodes.keys()) if hasattr(g, "nodes") else set()
    # Fallback: introspect the underlying graph (LangGraph versions differ).
    if not names:
        names = set(getattr(g, "get_graph", lambda: g)().nodes.keys())  # type: ignore
    assert "do_search" in names
