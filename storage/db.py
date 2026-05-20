"""Shared SQLite connection helper. One file per database; the file path comes
from $AI_COMPANY_DB (default ./data/ai_company.sqlite).

Each call returns a fresh connection so callers are responsible for closing.
We rely on WAL + autocommit-on-context-manager for concurrency.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def db_path() -> Path:
    p = Path(os.getenv("AI_COMPANY_DB", "./data/ai_company.sqlite"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # Perf: 64 MiB page cache + 256 MiB mmap — safe on 1-2 GB free-tier VMs.
    conn.execute("PRAGMA cache_size = -65536")    # negative = KiB → 64 MiB
    conn.execute("PRAGMA mmap_size = 268435456")  # 256 MiB
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
