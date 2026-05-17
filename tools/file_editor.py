"""File editor — TEMPORARY stub. Phase D replaces this with an Anthropic
tool-use loop that actually reads / writes / diffs files.

Until then, returns one stub CodeChange per file mentioned in the plan so the
graph downstream nodes (test / review / commit) have something to chew on.
"""
from __future__ import annotations

from typing import Iterable, List


def apply_plan(
    *,
    task: str,
    analysis: str,
    plan: List[dict],
    workflow_id: str | None = None,
) -> Iterable[dict]:
    out: list[dict] = []
    for step in plan or []:
        for f in step.get("files", []) or []:
            out.append({"path": f, "before": "", "after": "", "diff": f"# stub change for {f}"})
    return out
