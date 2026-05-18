"""Phase U: workflow + global cost budgets with circuit breaker.

`BudgetExceeded` is raised by `assert_within(workflow_id, state)` just
BEFORE each LLM call (see `nodes._chat`). The caller catches it and
returns a `budget_blocked` field on the state so the graph routes to
`checkpoint_budget`, which interrupts and lets the human raise the
ceiling or abort.

Two scopes, evaluated in this order:

1. workflow — `state["budget_ceiling_usd"]` (set by user via the
   checkpoint resume payload) > `$WORKFLOW_BUDGET_USD` env > unset (no
   limit). The state value lets a user raise the ceiling for one
   workflow without restarting the process.
2. global — `$GLOBAL_BUDGET_USD` env > unset. Applies across every
   workflow, summed over all time. Hitting it is a hard stop until the
   env var is changed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from storage import accounting


class BudgetExceeded(Exception):
    """Raised before an LLM call when current spend would push past a ceiling."""

    def __init__(self, scope: str, current: float, limit: float):
        self.scope = scope            # "workflow" | "global"
        self.current = current
        self.limit = limit
        super().__init__(f"{scope} budget exceeded: ${current:.4f} >= ${limit:.4f}")

    def to_dict(self) -> Dict[str, Any]:
        return {"scope": self.scope, "current": round(self.current, 6),
                "limit": round(self.limit, 6)}


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def workflow_limit(state: Dict[str, Any]) -> Optional[float]:
    """State override beats env. None = unlimited."""
    ceiling = state.get("budget_ceiling_usd") if isinstance(state, dict) else None
    if isinstance(ceiling, (int, float)) and ceiling > 0:
        return float(ceiling)
    return _env_float("WORKFLOW_BUDGET_USD")


def global_limit() -> Optional[float]:
    return _env_float("GLOBAL_BUDGET_USD")


def status(state: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshot for dashboards: current spend + ceilings + % used."""
    wf = state.get("workflow_id") if isinstance(state, dict) else None
    wf_cost = accounting.workflow_cost(wf) if wf else 0.0
    g_cost = accounting.total_cost()
    wf_lim = workflow_limit(state)
    g_lim = global_limit()
    return {
        "workflow_id": wf,
        "workflow_cost_usd": round(wf_cost, 6),
        "workflow_limit_usd": wf_lim,
        "workflow_pct": (wf_cost / wf_lim * 100.0) if wf_lim else None,
        "global_cost_usd": round(g_cost, 6),
        "global_limit_usd": g_lim,
        "global_pct": (g_cost / g_lim * 100.0) if g_lim else None,
    }


def assert_within(state: Dict[str, Any]) -> None:
    """Raise BudgetExceeded if the workflow or global budget is already past
    its ceiling. Called before each LLM call.
    """
    wf = state.get("workflow_id") if isinstance(state, dict) else None
    if wf:
        wf_lim = workflow_limit(state)
        if wf_lim is not None:
            cur = accounting.workflow_cost(wf)
            if cur >= wf_lim:
                raise BudgetExceeded("workflow", cur, wf_lim)
    g_lim = global_limit()
    if g_lim is not None:
        cur = accounting.total_cost()
        if cur >= g_lim:
            raise BudgetExceeded("global", cur, g_lim)
