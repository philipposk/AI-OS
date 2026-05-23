"""Retrospective ledger — captures workflow outcomes for self-improvement.

After each commit the orchestrator writes one row: which task, how many
test retries, whether the plan/code/commit was rejected at a checkpoint,
total cost, final verdict. The aggregate view feeds future prompt tuning
(DSPy) and model selection heuristics.

Schema is intentionally narrow — outcome facts only. Free-text retrospective
notes go in `notes`. Per-step transcripts live in the existing memory store.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from .db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS retrospectives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    workflow_id TEXT NOT NULL,
    task TEXT NOT NULL,
    verdict TEXT NOT NULL,            -- 'success' | 'rejected_plan' | 'rejected_code' | 'rejected_commit' | 'tests_failed' | 'budget_abort' | 'error'
    plan_steps INTEGER NOT NULL,
    test_retries INTEGER NOT NULL,
    tests_passed INTEGER,             -- 0/1/NULL
    files_touched INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    crew_mode INTEGER NOT NULL,       -- 0/1
    commit_sha TEXT,
    notes TEXT                        -- LLM-generated self-critique, may be empty
);
CREATE INDEX IF NOT EXISTS ix_retro_workflow ON retrospectives(workflow_id);
CREATE INDEX IF NOT EXISTS ix_retro_verdict ON retrospectives(verdict);
CREATE INDEX IF NOT EXISTS ix_retro_ts ON retrospectives(ts);
"""


@dataclass
class Retrospective:
    ts: str
    workflow_id: str
    task: str
    verdict: str
    plan_steps: int
    test_retries: int
    tests_passed: Optional[bool]
    files_touched: int
    cost_usd: float
    crew_mode: bool
    commit_sha: Optional[str] = None
    notes: str = ""


def _ensure(conn) -> None:
    conn.executescript(_SCHEMA)


def record(
    *,
    workflow_id: str,
    task: str,
    verdict: str,
    plan_steps: int = 0,
    test_retries: int = 0,
    tests_passed: Optional[bool] = None,
    files_touched: int = 0,
    cost_usd: float = 0.0,
    crew_mode: bool = False,
    commit_sha: Optional[str] = None,
    notes: str = "",
) -> Retrospective:
    entry = Retrospective(
        ts=datetime.now(timezone.utc).isoformat(),
        workflow_id=workflow_id,
        task=task,
        verdict=verdict,
        plan_steps=plan_steps,
        test_retries=test_retries,
        tests_passed=tests_passed,
        files_touched=files_touched,
        cost_usd=cost_usd,
        crew_mode=crew_mode,
        commit_sha=commit_sha,
        notes=notes,
    )
    with connect() as conn:
        _ensure(conn)
        conn.execute(
            "INSERT INTO retrospectives (ts, workflow_id, task, verdict, plan_steps, "
            "test_retries, tests_passed, files_touched, cost_usd, crew_mode, commit_sha, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.ts, entry.workflow_id, entry.task, entry.verdict,
                entry.plan_steps, entry.test_retries,
                None if entry.tests_passed is None else int(entry.tests_passed),
                entry.files_touched, entry.cost_usd, int(entry.crew_mode),
                entry.commit_sha, entry.notes,
            ),
        )
    return entry


def iter_entries(workflow_id: Optional[str] = None) -> Iterable[Retrospective]:
    with connect() as conn:
        _ensure(conn)
        if workflow_id:
            rows = conn.execute(
                "SELECT * FROM retrospectives WHERE workflow_id=? ORDER BY id ASC",
                (workflow_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM retrospectives ORDER BY id ASC").fetchall()
        for r in rows:
            tp = r["tests_passed"]
            yield Retrospective(
                ts=r["ts"], workflow_id=r["workflow_id"], task=r["task"],
                verdict=r["verdict"], plan_steps=r["plan_steps"],
                test_retries=r["test_retries"],
                tests_passed=None if tp is None else bool(tp),
                files_touched=r["files_touched"], cost_usd=r["cost_usd"],
                crew_mode=bool(r["crew_mode"]),
                commit_sha=r["commit_sha"], notes=r["notes"] or "",
            )


def aggregate() -> dict:
    """Counts by verdict + avg retries + avg cost. Feeds dashboard + DSPy training set."""
    with connect() as conn:
        _ensure(conn)
        rows = conn.execute(
            "SELECT verdict, COUNT(*) c, AVG(test_retries) r, AVG(cost_usd) cost "
            "FROM retrospectives GROUP BY verdict"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) n FROM retrospectives").fetchone()
    by_verdict = {
        r["verdict"]: {
            "count": r["c"],
            "avg_retries": round(r["r"] or 0.0, 2),
            "avg_cost_usd": round(r["cost"] or 0.0, 6),
        }
        for r in rows
    }
    return {"total": total["n"] if total else 0, "by_verdict": by_verdict}
