"""Per-(provider, model, task_type) success-rate aggregation.

Joins the `ledger` table (which records every LLM call with its workflow_id,
provider, model, task_type, cost) with the `retrospectives` table (which
records the final verdict per workflow_id). The result is a table:

    provider | model | task_type | n_workflows | success_rate | avg_retries | avg_cost

That answers "which model is actually working best for the `plan` task?"
and "is Groq's free llama as reliable as Anthropic's Haiku for `analyze`?"

Two consumers:
  1. `cli.py model-perf` — human report.
  2. `router.router.ModelRouter.resolve()` when `$USE_LEARNED_MODELS=1` —
     auto-pick the highest-success model for a task_type, falling back to
     env / DEFAULT_TASK_MODELS when no learned data exists.

Only counts workflows where the model was used on the matching task_type
during a workflow that produced a retrospective row. New workflows with no
retrospective yet are ignored — keeps the signal clean.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .accounting import _SCHEMA as _LEDGER_SCHEMA
from .db import connect
from .retrospectives import _SCHEMA as _RETRO_SCHEMA


@dataclass
class ModelPerformance:
    provider: str
    model: str
    task_type: str
    n_workflows: int          # distinct workflow_ids where model+task_type appeared
    n_successes: int          # of those, how many had verdict='success'
    success_rate: float       # n_successes / n_workflows
    avg_retries: float        # mean test_retries across those workflows
    avg_cost_per_call: float  # mean cost_usd of the ledger rows


def _ensure(conn) -> None:
    conn.executescript(_LEDGER_SCHEMA)
    conn.executescript(_RETRO_SCHEMA)


_REPORT_SQL = """
WITH calls AS (
    SELECT provider, model, task_type, workflow_id, cost_usd
    FROM ledger
    WHERE workflow_id IS NOT NULL AND workflow_id != ''
),
retros AS (
    SELECT workflow_id, verdict, test_retries
    FROM retrospectives
),
joined AS (
    SELECT c.provider, c.model, c.task_type, c.workflow_id, c.cost_usd,
           r.verdict, r.test_retries
    FROM calls c
    JOIN retros r ON r.workflow_id = c.workflow_id
)
SELECT provider, model, task_type,
       COUNT(DISTINCT workflow_id) AS n_workflows,
       SUM(CASE WHEN verdict='success' THEN 1 ELSE 0 END) AS n_successes,
       AVG(test_retries) AS avg_retries,
       AVG(cost_usd)     AS avg_cost
FROM joined
GROUP BY provider, model, task_type
"""


def report(task_type: Optional[str] = None) -> List[ModelPerformance]:
    """Return per-(provider, model, task_type) performance rows.

    `task_type` filter narrows the rows. Returned list is sorted by
    success_rate descending, then by n_workflows descending — so the most
    reliable, well-tested combinations appear first.
    """
    out: List[ModelPerformance] = []
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute(_REPORT_SQL).fetchall()
    for r in rows:
        if task_type and r["task_type"] != task_type:
            continue
        n_workflows = int(r["n_workflows"] or 0)
        n_successes = int(r["n_successes"] or 0)
        if n_workflows == 0:
            continue
        # Workflows can have several calls of the same task_type; SUM counts
        # rows, not distinct workflows, so we computed n_successes via
        # CASE which can over-count. Cap to n_workflows so the rate stays
        # in [0,1].
        n_successes = min(n_successes, n_workflows)
        out.append(ModelPerformance(
            provider=r["provider"],
            model=r["model"],
            task_type=r["task_type"],
            n_workflows=n_workflows,
            n_successes=n_successes,
            success_rate=n_successes / n_workflows,
            avg_retries=float(r["avg_retries"] or 0.0),
            avg_cost_per_call=float(r["avg_cost"] or 0.0),
        ))
    out.sort(key=lambda p: (-p.success_rate, -p.n_workflows, p.avg_cost_per_call))
    return out


def best_model_for(
    task_type: str,
    *,
    min_workflows: int = 5,
    max_cost_per_call: Optional[float] = None,
) -> Optional[Tuple[str, str]]:
    """Return (provider, model) with the highest success rate for `task_type`.

    Constraints:
      * at least `min_workflows` distinct workflows of data — keeps us from
        chasing noise on a model that's only been tried twice.
      * if `max_cost_per_call` is set (or `$BEST_MODEL_MAX_COST_PER_CALL`),
        ignore rows whose avg_cost exceeds it.

    Returns None when no eligible row exists. Callers fall back to env /
    DEFAULT_TASK_MODELS in that case.
    """
    if max_cost_per_call is None:
        raw = os.getenv("BEST_MODEL_MAX_COST_PER_CALL", "").strip()
        if raw:
            try:
                max_cost_per_call = float(raw)
            except ValueError:
                max_cost_per_call = None

    rows = report(task_type=task_type)
    eligible = [
        r for r in rows
        if r.n_workflows >= min_workflows
        and (max_cost_per_call is None or r.avg_cost_per_call <= max_cost_per_call)
    ]
    if not eligible:
        return None
    top = eligible[0]
    return top.provider, top.model


def summary() -> Dict[str, dict]:
    """One-shot dashboard payload: per-task_type best model + counts."""
    rows = report()
    by_task: Dict[str, List[ModelPerformance]] = {}
    for r in rows:
        by_task.setdefault(r.task_type, []).append(r)
    return {
        tt: {
            "rows": [
                {
                    "provider": p.provider,
                    "model": p.model,
                    "n_workflows": p.n_workflows,
                    "success_rate": round(p.success_rate, 4),
                    "avg_retries": round(p.avg_retries, 2),
                    "avg_cost_per_call": round(p.avg_cost_per_call, 6),
                }
                for p in rs
            ],
            "best": (rs[0].provider, rs[0].model) if rs else None,
        }
        for tt, rs in by_task.items()
    }
