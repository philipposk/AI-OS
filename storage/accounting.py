"""Token + cost ledger backed by SQLite.

Public API (stable across Phase B → E swap):
    record(...) -> LedgerEntry
    iter_entries() -> Iterable[LedgerEntry]
    report() -> dict
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from .db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    task_type TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    workflow_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_ledger_provider ON ledger(provider);
CREATE INDEX IF NOT EXISTS ix_ledger_workflow ON ledger(workflow_id);
CREATE INDEX IF NOT EXISTS ix_ledger_ts ON ledger(ts);
"""


@dataclass
class LedgerEntry:
    ts: str
    provider: str
    model: str
    task_type: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    workflow_id: Optional[str] = None


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def record(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    task_type: str = "unknown",
    workflow_id: Optional[str] = None,
) -> LedgerEntry:
    # Lazy import to avoid the circular `storage.accounting → router.costs →
    # router → storage.accounting` chain when tests import accounting first.
    from router.costs import estimate_cost_usd
    entry = LedgerEntry(
        ts=datetime.now(timezone.utc).isoformat(),
        provider=provider,
        model=model,
        task_type=task_type,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=estimate_cost_usd(model, prompt_tokens, completion_tokens),
        workflow_id=workflow_id,
    )
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO ledger (ts, provider, model, task_type, prompt_tokens, completion_tokens, cost_usd, workflow_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.ts,
                entry.provider,
                entry.model,
                entry.task_type,
                entry.prompt_tokens,
                entry.completion_tokens,
                entry.cost_usd,
                entry.workflow_id,
            ),
        )
    return entry


def iter_entries(workflow_id: Optional[str] = None) -> Iterable[LedgerEntry]:
    with connect() as conn:
        _ensure(conn)
        if workflow_id:
            rows = conn.execute(
                "SELECT * FROM ledger WHERE workflow_id=? ORDER BY id ASC", (workflow_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM ledger ORDER BY id ASC").fetchall()
        for r in rows:
            yield LedgerEntry(
                ts=r["ts"],
                provider=r["provider"],
                model=r["model"],
                task_type=r["task_type"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                cost_usd=r["cost_usd"],
                workflow_id=r["workflow_id"],
            )


def workflow_cost(workflow_id: str) -> float:
    """Sum cost_usd for one workflow. Phase U: feeds the budget circuit breaker."""
    with connect() as conn:
        _ensure(conn)
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM ledger WHERE workflow_id=?",
            (workflow_id,),
        ).fetchone()
    return float(row["s"]) if row else 0.0


def total_cost() -> float:
    """Sum cost_usd across all workflows ever. Phase U: feeds the global budget."""
    with connect() as conn:
        _ensure(conn)
        row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) AS s FROM ledger").fetchone()
    return float(row["s"]) if row else 0.0


def report(workflow_id: Optional[str] = None) -> dict:
    by_provider: dict[str, dict] = {}
    total_cost = 0.0
    total_calls = 0
    total_in = 0
    total_out = 0
    for e in iter_entries(workflow_id):
        b = by_provider.setdefault(
            e.provider, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
        )
        b["calls"] += 1
        b["prompt_tokens"] += e.prompt_tokens
        b["completion_tokens"] += e.completion_tokens
        b["cost_usd"] += e.cost_usd
        total_calls += 1
        total_in += e.prompt_tokens
        total_out += e.completion_tokens
        total_cost += e.cost_usd
    return {
        "total_calls": total_calls,
        "total_prompt_tokens": total_in,
        "total_completion_tokens": total_out,
        "total_cost_usd": round(total_cost, 6),
        "by_provider": {k: {**v, "cost_usd": round(v["cost_usd"], 6)} for k, v in by_provider.items()},
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
