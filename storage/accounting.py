"""Token + cost ledger.

Phase B impl: append JSON lines to data/accounting.jsonl. Phase E swaps to sqlite.
Interface kept stable so the swap is mechanical.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from router.costs import estimate_cost_usd

_LOCK = threading.Lock()


def _ledger_path() -> Path:
    p = Path(os.getenv("AI_COMPANY_ACCOUNTING_LOG", "./data/accounting.jsonl"))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class LedgerEntry:
    ts: str
    provider: str
    model: str
    task_type: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    workflow_id: str | None = None


def record(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    task_type: str = "unknown",
    workflow_id: str | None = None,
) -> LedgerEntry:
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
    with _LOCK, _ledger_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
    return entry


def iter_entries() -> Iterable[LedgerEntry]:
    p = _ledger_path()
    if not p.exists():
        return
    with p.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield LedgerEntry(**json.loads(line))


def report() -> dict:
    by_provider: dict[str, dict] = {}
    total_cost = 0.0
    total_calls = 0
    total_in = 0
    total_out = 0
    for e in iter_entries():
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
