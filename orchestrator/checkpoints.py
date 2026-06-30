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


def review_budget_checkpoint(state: GraphState) -> Dict[str, Any]:
    """Phase U: human approves "raise ceiling" or aborts when a budget trips.

    Resume payload shapes:
        {"approved": False}                                  → END the graph
        {"approved": True}                                   → retry with the
            existing ceiling cleared just for this node (rare; use carefully)
        {"approved": True, "raise_to": 1.50}                  → bump
            state["budget_ceiling_usd"] to $1.50 and resume `budget_blocked.next`
    """
    blocked = state.get("budget_blocked") or {}
    # Preserve the resume target in a separate field BEFORE the interrupt so
    # _after_budget can still read it after _apply_budget_decision clears
    # budget_blocked (the two writes happen in one state merge).
    resume_node = blocked.get("next")
    decision = interrupt(
        {
            "kind": "budget_exceeded",
            "scope": blocked.get("scope"),
            "current_usd": blocked.get("current"),
            "limit_usd": blocked.get("limit"),
            "next_node": resume_node,
            "workflow_id": state.get("workflow_id"),
        }
    )
    result = _apply_budget_decision(decision)
    result["budget_resume_node"] = resume_node if result.get("approved") else None
    return result


def _apply_budget_decision(decision: Any) -> Dict[str, Any]:
    if isinstance(decision, dict):
        if not decision.get("approved"):
            return {"approved": False,
                    "rejection_reason": decision.get("reason", "budget abort"),
                    "budget_blocked": None}
        out: Dict[str, Any] = {"approved": True, "budget_blocked": None}
        raise_to = decision.get("raise_to")
        if isinstance(raise_to, (int, float)) and raise_to > 0:
            out["budget_ceiling_usd"] = float(raise_to)
        return out
    if decision is True:
        return {"approved": True, "budget_blocked": None}
    return {"approved": False, "rejection_reason": "budget abort", "budget_blocked": None}


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
