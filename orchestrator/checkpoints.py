"""Human-in-the-loop interrupt nodes.

Each checkpoint pauses the graph via langgraph.types.interrupt(payload).
Resuming the graph with Command(resume=<dict>) returns that dict here so we
can write the decision back into state.
"""
from __future__ import annotations

from typing import Any, Dict

from langgraph.types import interrupt

from .state import GraphState


def review_plan_checkpoint(state: GraphState) -> Dict[str, Any]:
    decision = interrupt(
        {
            "kind": "review_plan",
            "task": state.get("task"),
            "analysis": state.get("analysis"),
            "plan": state.get("plan"),
        }
    )
    return _apply_decision(decision)


def review_code_checkpoint(state: GraphState) -> Dict[str, Any]:
    decision = interrupt(
        {
            "kind": "review_code",
            "code_changes": state.get("code_changes"),
            "test_results": state.get("test_results"),
            "review_summary": state.get("review_summary"),
        }
    )
    return _apply_decision(decision)


def review_commit_checkpoint(state: GraphState) -> Dict[str, Any]:
    decision = interrupt(
        {
            "kind": "review_commit",
            "commit_message": state.get("commit_message"),
            "code_changes": state.get("code_changes"),
        }
    )
    return _apply_decision(decision)


def _apply_decision(decision: Any) -> Dict[str, Any]:
    """Normalise the resume payload into state writes.

    Accepted shapes:
        True / False                              → approved bool
        {"approved": bool, "reason": str?, "commit_message": str?}
    """
    if isinstance(decision, bool):
        return {"approved": decision, "rejection_reason": None}
    if isinstance(decision, dict):
        out: Dict[str, Any] = {
            "approved": bool(decision.get("approved", False)),
            "rejection_reason": decision.get("reason"),
        }
        if "commit_message" in decision:
            out["commit_message"] = decision["commit_message"]
        return out
    # Default: treat anything else as rejection so the graph stops safely.
    return {"approved": False, "rejection_reason": f"unexpected resume payload: {decision!r}"}
