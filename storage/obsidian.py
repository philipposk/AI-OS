"""Obsidian vault mirror.

If `$OBSIDIAN_VAULT_PATH` points at a vault directory, every memory.add()
also writes a markdown file with YAML frontmatter into
`<vault>/ai_company/<workflow_id-or-misc>/<id>-<slug>.md`.

Vault search:
    obsidian_search(query) walks .md files under the configured root and
    returns hits with substring matches (cheap, no FTS) — used as a
    supplement to memory.search() if the user wants notes outside their
    workflow runs (manual notes, design docs) to influence analyses.

Disable with `$OBSIDIAN_VAULT_PATH` unset or `$OBSIDIAN_DISABLED=1`.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def vault_root() -> Optional[Path]:
    if os.getenv("OBSIDIAN_DISABLED", "").lower() in ("1", "true", "yes"):
        return None
    raw = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_dir():
        logger.warning("OBSIDIAN_VAULT_PATH=%s is not a directory; vault mirror disabled", p)
        return None
    return p


def _slug(text: str, max_len: int = 50) -> str:
    s = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return (s or "note")[:max_len]


_YAML_SPECIAL = (":", "#", '"', "\\", "\n")


def _needs_quote(s: str) -> bool:
    return any(c in s for c in _YAML_SPECIAL)


def _yaml_dump(d: dict) -> str:
    """Tiny YAML emitter for flat string/list frontmatter — avoids a pyyaml dep."""
    lines: list[str] = []
    for k, v in d.items():
        if v is None:
            continue
        if isinstance(v, list):
            if not v:
                continue
            lines.append(f"{k}:")
            for item in v:
                s = str(item)
                rendered = json.dumps(s) if _needs_quote(s) else s
                lines.append(f"  - {rendered}")
        else:
            sv = str(v)
            quoted = json.dumps(sv) if _needs_quote(sv) else sv
            lines.append(f"{k}: {quoted}")
    return "\n".join(lines)


def _frontmatter(meta: dict) -> str:
    body = _yaml_dump(meta)
    return f"---\n{body}\n---\n"


@dataclass
class WriteResult:
    path: Path
    bytes_written: int


def mirror_note(*, doc_id: int, kind: str, text: str, workflow_id: Optional[str] = None, meta: Optional[dict] = None, created_at: Optional[str] = None) -> Optional[WriteResult]:
    """Write a single memory row into the vault. Returns None if mirror disabled."""
    root = vault_root()
    if root is None:
        return None
    bucket = workflow_id or "misc"
    folder = root / "ai_company" / _slug(bucket, 80)
    folder.mkdir(parents=True, exist_ok=True)

    slug = _slug(text.splitlines()[0] if text else f"{kind}-{doc_id}")
    path = folder / f"{doc_id:06d}-{slug}.md"

    fm = {
        "id": doc_id,
        "kind": kind,
        "workflow_id": workflow_id or "",
        "created_at": created_at or "",
        "tags": ["ai_company", f"kind/{kind}"] + (meta.get("tags") if isinstance(meta, dict) and meta.get("tags") else []),
    }
    if isinstance(meta, dict):
        # Add a few common scalar keys directly to frontmatter
        for k in ("task", "files"):
            if k in meta:
                fm[k] = meta[k]

    body = _frontmatter(fm) + "\n" + text + "\n"
    data = body.encode("utf-8")
    path.write_bytes(data)
    return WriteResult(path=path, bytes_written=len(data))


def search_vault(query: str, *, limit: int = 10, max_bytes_per_file: int = 200_000) -> List[dict]:
    """Cheap substring search over the entire vault, not just our subfolder.

    Returns up to `limit` matches as dicts {path, snippet, score} ordered by
    occurrence count. Skips files larger than `max_bytes_per_file` to keep
    memory bounded.
    """
    root = vault_root()
    if root is None or not query.strip():
        return []
    terms = [t.lower() for t in query.split() if t]
    hits: list[dict] = []
    for p in root.rglob("*.md"):
        try:
            if p.stat().st_size > max_bytes_per_file:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score == 0:
            continue
        idx = min((low.find(t) for t in terms if t in low), default=0)
        start = max(0, idx - 80)
        snippet = text[start : start + 240].replace("\n", " ")
        hits.append({"path": str(p.relative_to(root)), "score": score, "snippet": snippet})
    hits.sort(key=lambda h: -h["score"])
    return hits[:limit]


def bulk_export(*, kinds: Optional[Iterable[str]] = None) -> int:
    """Mirror every existing memory_docs row that doesn't already have a file. Returns count written."""
    root = vault_root()
    if root is None:
        return 0
    from .db import connect

    sql = "SELECT id, kind, workflow_id, meta, text, created_at FROM memory_docs"
    params: tuple = ()
    if kinds:
        placeholders = ",".join("?" for _ in kinds)
        sql += f" WHERE kind IN ({placeholders})"
        params = tuple(kinds)
    sql += " ORDER BY id"

    written = 0
    with connect() as conn:
        for row in conn.execute(sql, params).fetchall():
            res = mirror_note(
                doc_id=row["id"],
                kind=row["kind"],
                text=row["text"],
                workflow_id=row["workflow_id"],
                meta=json.loads(row["meta"]) if row["meta"] else {},
                created_at=row["created_at"],
            )
            if res is not None:
                written += 1
    return written
