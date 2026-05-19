"""LLM-based ticket classifier for passive Slack ingestion.

`classify(text)` asks the router whether a Slack message describes a
coding/build task and returns `(ticket: bool, summary: str, confidence: float)`.

Cheap on purpose: uses task_type="simple" (cheapest free-tier model in the
DEFAULT_TASK_MODELS table) so passive scanning of an active channel costs
fractions of a cent per message.

Tunables (env):
    TICKET_CONFIDENCE_MIN   float, default 0.6 — below this we treat as
                            "not a ticket" even if the model said yes.
    TICKET_MAX_INPUT_CHARS  int, default 1200 — trim long messages.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Tuple

from router import ModelRouter

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You classify Slack messages. Decide whether the message describes a "
    "concrete software engineering task that an autonomous coding agent "
    "should attempt — a bug to fix, a feature to add, a refactor to do, a "
    "script to write. Casual questions, opinions, status updates, social "
    "chatter, and pure discussion are NOT tickets. Output STRICT JSON only "
    'with keys: {"ticket": bool, "summary": str, "confidence": float in [0,1]}. '
    "`summary` must be one short imperative sentence describing the task "
    "(<=120 chars). Reply ONLY with the JSON object, no commentary."
)


@dataclass
class Classification:
    ticket: bool
    summary: str
    confidence: float
    raw: str


_router_singleton: ModelRouter | None = None


def _router() -> ModelRouter:
    global _router_singleton
    if _router_singleton is None:
        _router_singleton = ModelRouter()
    return _router_singleton


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse(text: str) -> Tuple[bool, str, float]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    m = _JSON_OBJ_RE.search(text)
    raw = m.group(0) if m else text
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("ticket classifier JSON parse failed: %s; raw=%r", e, text[:200])
        return False, "", 0.0
    ticket = bool(obj.get("ticket", False))
    summary = str(obj.get("summary", ""))[:200]
    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return ticket, summary, max(0.0, min(1.0, confidence))


def classify(text: str, *, workflow_id: str | None = None) -> Classification:
    """Returns the parsed classification. Never raises — on any failure returns
    a non-ticket classification."""
    max_chars = int(os.getenv("TICKET_MAX_INPUT_CHARS", "1200"))
    snippet = (text or "").strip()[:max_chars]
    if not snippet:
        return Classification(False, "", 0.0, "")
    try:
        res = _router().chat(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": snippet},
            ],
            task_type="simple",
            max_tokens=160,
            temperature=0.0,
            workflow_id=workflow_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("ticket classifier router error: %s", e)
        return Classification(False, "", 0.0, "")
    ticket, summary, confidence = _parse(res.text)
    floor = float(os.getenv("TICKET_CONFIDENCE_MIN", "0.6"))
    if confidence < floor:
        ticket = False
    return Classification(ticket=ticket, summary=summary, confidence=confidence, raw=res.text)
