"""SQLite-backed task queue.

States: pending → in_progress → (done | failed | cancelled).
`pop()` atomically claims the highest-priority pending task.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    workflow_id TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_status_prio ON tasks(status, priority DESC, id ASC);
"""


@dataclass
class Task:
    id: int
    task: str
    priority: int
    status: str
    workflow_id: Optional[str]
    metadata: dict
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    error: Optional[str]

    @classmethod
    def from_row(cls, row) -> "Task":
        return cls(
            id=row["id"],
            task=row["task"],
            priority=row["priority"],
            status=row["status"],
            workflow_id=row["workflow_id"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            error=row["error"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema(conn) -> None:
    conn.executescript(_SCHEMA)


def push(task: str, *, priority: int = 0, workflow_id: Optional[str] = None, metadata: Optional[dict] = None) -> Task:
    with connect() as conn:
        _ensure_schema(conn)
        cur = conn.execute(
            "INSERT INTO tasks (task, priority, status, workflow_id, metadata, created_at) "
            "VALUES (?, ?, 'pending', ?, ?, ?) RETURNING *",
            (task, priority, workflow_id, json.dumps(metadata or {}), _now()),
        )
        row = cur.fetchone()
        return Task.from_row(row)


def pop() -> Optional[Task]:
    """Atomically claim the highest-priority pending task. Returns None if empty."""
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status='pending' "
                "ORDER BY priority DESC, id ASC LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            conn.execute(
                "UPDATE tasks SET status='in_progress', started_at=? WHERE id=? AND status='pending'",
                (_now(), row["id"]),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone()
            conn.execute("COMMIT")
            return Task.from_row(row)
        except Exception:
            conn.execute("ROLLBACK")
            raise


def mark_done(task_id: int) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE tasks SET status='done', finished_at=? WHERE id=?",
            (_now(), task_id),
        )


def mark_failed(task_id: int, error: str) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE tasks SET status='failed', finished_at=?, error=? WHERE id=?",
            (_now(), error[:2000], task_id),
        )


def cancel(task_id: int) -> None:
    with connect() as conn:
        _ensure_schema(conn)
        conn.execute(
            "UPDATE tasks SET status='cancelled', finished_at=? WHERE id=? AND status IN ('pending','in_progress')",
            (_now(), task_id),
        )


def list_tasks(status: Optional[str] = None, limit: int = 50) -> List[Task]:
    with connect() as conn:
        _ensure_schema(conn)
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Task.from_row(r) for r in rows]


def size(status: str = "pending") -> int:
    with connect() as conn:
        _ensure_schema(conn)
        return conn.execute("SELECT COUNT(*) FROM tasks WHERE status=?", (status,)).fetchone()[0]


def status_counts() -> dict:
    with connect() as conn:
        _ensure_schema(conn)
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}
