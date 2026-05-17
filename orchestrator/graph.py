"""Top-level LangGraph: analyze → plan → [HUMAN] → code → test → [HUMAN] → review → [HUMAN] → commit."""
from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from .checkpoints import (
    review_code_checkpoint,
    review_commit_checkpoint,
    review_plan_checkpoint,
)
from .nodes import (
    analyze_node,
    code_node,
    commit_node,
    plan_node,
    review_node,
    test_node,
)
from .state import GraphState

MAX_TEST_RETRIES = 3


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

    g.add_edge(START, "do_analyze")
    g.add_edge("do_analyze", "do_plan")
    g.add_edge("do_plan", "checkpoint_plan")
    g.add_conditional_edges("checkpoint_plan", _after_plan_review, {"do_code": "do_code", END: END})
    g.add_edge("do_code", "do_test")
    g.add_edge("do_code_retry", "do_code")
    g.add_conditional_edges(
        "do_test",
        _after_test,
        {"do_review": "do_review", "do_code_retry": "do_code_retry", "checkpoint_code": "checkpoint_code"},
    )
    g.add_conditional_edges("checkpoint_code", _after_code_review, {"do_review": "do_review", END: END})
    g.add_edge("do_review", "checkpoint_commit")
    g.add_conditional_edges("checkpoint_commit", _after_commit_review, {"do_commit": "do_commit", END: END})
    g.add_edge("do_commit", END)

    return g.compile(checkpointer=checkpointer or MemorySaver())


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
