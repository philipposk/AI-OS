"""Top-level LangGraph: analyze → plan → [HUMAN] → code → test → [HUMAN] → review → [HUMAN] → commit."""
from __future__ import annotations

import logging
import os
import sqlite3
import uuid
from typing import Any, Dict, Iterable, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

_log = logging.getLogger(__name__)


def default_checkpointer() -> Any:
    """Return a persistent SqliteSaver if `LANGGRAPH_CHECKPOINT_DB` is set,
    else fall back to in-memory `MemorySaver`.

    Persistent checkpointing means workflows survive process restarts —
    users waiting at a human-in-the-loop checkpoint can resume after the
    server is bounced. MemorySaver is fine for tests and one-shot CLI runs.
    """
    db_path = os.getenv("LANGGRAPH_CHECKPOINT_DB", "").strip()
    if not db_path:
        return MemorySaver()
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        _log.warning("LANGGRAPH_CHECKPOINT_DB=%s but langgraph-checkpoint-sqlite "
                     "not installed; falling back to MemorySaver", db_path)
        return MemorySaver()
    # check_same_thread=False so Streamlit / FastAPI worker threads can share it.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    return SqliteSaver(conn)

from .checkpoints import (
    review_budget_checkpoint,
    review_code_checkpoint,
    review_commit_checkpoint,
    review_plan_checkpoint,
)
from .nodes import (
    analyze_node,
    code_node,
    commit_node,
    plan_node,
    retrospective_node,
    review_node,
    search_node,
    test_node,
)
from .state import GraphState

MAX_TEST_RETRIES = 3


def _budget_or(passthrough: str):
    """Phase U: if the previous node tripped a budget, divert to the budget
    checkpoint; otherwise stay on the canonical edge. Used as the condition
    function for every LLM-producing node.
    """
    def _route(state: GraphState) -> str:
        return "checkpoint_budget" if state.get("budget_blocked") else passthrough
    return _route


def _after_budget(state: GraphState) -> str:
    """Resume from budget checkpoint back to whichever node was blocked,
    or END if the human aborted.
    """
    if not state.get("approved"):
        return END
    blocked = state.get("budget_blocked") or {}
    return blocked.get("next") or END


def _after_plan_review(state: GraphState) -> str:
    return "do_code" if state.get("approved") else END


def _after_test(state: GraphState) -> str:
    tr = state.get("test_results") or {}
    if tr.get("passed"):
        return "do_review"
    if (state.get("retries") or 0) >= MAX_TEST_RETRIES:
        return "checkpoint_code"  # let the human decide what to do with a failing build
    return "do_code_retry"


def _bump_retry(state: GraphState) -> Dict[str, Any]:
    return {"retries": (state.get("retries") or 0) + 1}


def _after_code_review(state: GraphState) -> str:
    return "do_review" if state.get("approved") else END


def _after_commit_review(state: GraphState) -> str:
    return "do_commit" if state.get("approved") else END


def build_graph(checkpointer: Optional[Any] = None):
    g = StateGraph(GraphState)
    g.add_node("do_analyze", analyze_node)
    g.add_node("do_plan", plan_node)
    g.add_node("checkpoint_plan", review_plan_checkpoint)
    g.add_node("do_code", code_node)
    g.add_node("do_code_retry", _bump_retry)
    g.add_node("do_test", test_node)
    g.add_node("checkpoint_code", review_code_checkpoint)
    g.add_node("do_review", review_node)
    g.add_node("checkpoint_commit", review_commit_checkpoint)
    g.add_node("do_commit", commit_node)
    g.add_node("do_retrospective", retrospective_node)
    g.add_node("checkpoint_budget", review_budget_checkpoint)
    g.add_node("do_search", search_node)

    g.add_edge(START, "do_analyze")
    # Phase V: after analyze, optionally hit web search before planning. The
    # _budget_or wrapper still applies — if the prior node tripped a budget,
    # we divert to checkpoint_budget regardless of search_enabled.
    g.add_conditional_edges("do_analyze", _budget_or("do_search"),
                            {"do_search": "do_search", "checkpoint_budget": "checkpoint_budget"})
    g.add_edge("do_search", "do_plan")
    g.add_conditional_edges("do_plan", _budget_or("checkpoint_plan"),
                            {"checkpoint_plan": "checkpoint_plan", "checkpoint_budget": "checkpoint_budget"})
    g.add_conditional_edges("checkpoint_plan", _after_plan_review, {"do_code": "do_code", END: END})
    g.add_conditional_edges("do_code", _budget_or("do_test"),
                            {"do_test": "do_test", "checkpoint_budget": "checkpoint_budget"})
    g.add_edge("do_code_retry", "do_code")
    g.add_conditional_edges(
        "do_test",
        _after_test,
        {"do_review": "do_review", "do_code_retry": "do_code_retry", "checkpoint_code": "checkpoint_code"},
    )
    g.add_conditional_edges("checkpoint_code", _after_code_review, {"do_review": "do_review", END: END})
    g.add_conditional_edges("do_review", _budget_or("checkpoint_commit"),
                            {"checkpoint_commit": "checkpoint_commit", "checkpoint_budget": "checkpoint_budget"})
    g.add_conditional_edges("checkpoint_commit", _after_commit_review, {"do_commit": "do_commit", END: END})
    # Phase Z: every committed workflow runs a retrospective for self-improvement.
    # Set $RETROSPECTIVE_DISABLED=1 to skip (e.g. CI runs that don't want LLM cost).
    g.add_edge("do_commit", "do_retrospective")
    g.add_edge("do_retrospective", END)
    g.add_conditional_edges(
        "checkpoint_budget",
        _after_budget,
        {"do_analyze": "do_analyze", "do_plan": "do_plan", "do_code": "do_code",
         "do_review": "do_review", END: END},
    )

    return g.compile(checkpointer=checkpointer or default_checkpointer())


# ---------- convenience runner ----------


def new_workflow_id() -> str:
    return uuid.uuid4().hex[:12]


def start(task: str, *, workflow_id: Optional[str] = None, graph=None) -> Iterable[Dict[str, Any]]:
    """Drive the graph from a fresh start, yielding events until first interrupt or END.

    Returns the event stream from `graph.stream(...)`. Caller is responsible for
    inspecting `__interrupt__` events and resuming via `resume(...)`.
    """
    graph = graph or build_graph()
    wf = workflow_id or new_workflow_id()
    config = {"configurable": {"thread_id": wf}}
    return graph.stream({"task": task, "workflow_id": wf}, config=config)


def resume(workflow_id: str, decision: Any, *, graph=None) -> Iterable[Dict[str, Any]]:
    graph = graph or build_graph()
    config = {"configurable": {"thread_id": workflow_id}}
    return graph.stream(Command(resume=decision), config=config)
