"""Pydantic + Instructor — typed structured outputs for plan / review.

The existing `_extract_json_array` + `json_repair` path is robust but
forces every plan field to be `Any` and parsing errors are caught only
after the fact. This module gives the LLM a Pydantic schema upfront via
`instructor.from_litellm`, so the model returns validated objects and we
never have to repair JSON at all.

Opt-in: `$USE_STRUCTURED_OUTPUTS=1`. When off (default), nodes fall back
to the legacy `_chat` + `_extract_json_array` path so behavior is byte-
for-byte identical to today's runs.

When on:
  * Requires litellm + instructor + an LLM provider key the litellm router
    understands (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, ...).
  * Model is `$STRUCTURED_MODEL` (default `openai/gpt-4o-mini`).
  * On any error we log a warning and return None so the node falls back.

The Pydantic models intentionally mirror the existing TypedDicts in
`state.py` so swap-in is a straight `.dict()` away.
"""
from __future__ import annotations

import logging
import os
from typing import List, Optional

logger = logging.getLogger(__name__)


def _structured_enabled() -> bool:
    return os.getenv("USE_STRUCTURED_OUTPUTS", "").strip().lower() in ("1", "true", "yes", "on")


def _structured_model() -> str:
    return os.getenv("STRUCTURED_MODEL", "openai/gpt-4o-mini")


def _try_imports():
    """Lazy: return (instructor, litellm, BaseModel, Field) or None if missing."""
    try:
        import instructor
        import litellm
        from pydantic import BaseModel, Field
        return instructor, litellm, BaseModel, Field
    except ImportError as e:
        logger.info("structured outputs disabled — missing dep: %s", e)
        return None


def _build_models():
    """Define Pydantic models lazily so we don't crash if pydantic is missing."""
    imports = _try_imports()
    if imports is None:
        return None
    _instructor, _litellm, BaseModel, Field = imports

    class PlanStepModel(BaseModel):
        title: str = Field(..., max_length=200,
                           description="Imperative phrase, verb first, ≤80 chars ideally.")
        detail: str = Field(..., description="1-2 sentences explaining WHY/HOW. No restating the title.")
        files: List[str] = Field(default_factory=list,
                                 description="Relative paths the step will touch; [] if no file is touched yet.")

    class PlannedTask(BaseModel):
        steps: List[PlanStepModel] = Field(..., min_length=1, max_length=5,
                                            description="≤5 reviewable, concrete steps.")

    class ReviewSummary(BaseModel):
        summary: str = Field(..., max_length=600,
                             description="≤60 words. What the diff does + one residual risk or 'no residual risk'.")

    return {
        "PlanStepModel": PlanStepModel,
        "PlannedTask": PlannedTask,
        "ReviewSummary": ReviewSummary,
        "instructor": _instructor,
        "litellm": _litellm,
    }


def structured_plan(
    *,
    task: str,
    analysis: str,
    search_block: str = "",
    workflow_id: Optional[str] = None,
) -> Optional[List[dict]]:
    """Return a list of plan-step dicts via instructor, or None on any error.

    Caller pattern in nodes.plan_node:
        if _structured_enabled():
            steps = structured_plan(task=..., analysis=..., search_block=...)
            if steps is not None:
                return {"plan": steps}
        # fall through to legacy _chat + _extract_json_array
    """
    if not _structured_enabled():
        return None
    bundle = _build_models()
    if bundle is None:
        return None
    instructor = bundle["instructor"]
    litellm = bundle["litellm"]
    PlannedTask = bundle["PlannedTask"]

    system = (
        "Plan a small, reviewable code change. Return a `PlannedTask` with ≤5 "
        "steps. Each step: title (imperative, verb-first), detail (1-2 sentences, "
        "WHY/HOW), files (paths). No filler steps like 'review requirements'."
    )
    user = f"Task: {task}\n\nAnalysis:\n{analysis}"
    if search_block:
        user = search_block + "\n\n" + user

    # Single try/except spans both the LLM call AND the result-extraction so
    # ANY shape surprise (missing .steps, model_dump failure, etc.) falls
    # back cleanly rather than crashing the plan_node.
    try:
        client = instructor.from_litellm(litellm.completion)
        result: "PlannedTask" = client.messages.create(  # type: ignore[name-defined]
            model=_structured_model(),
            response_model=PlannedTask,
            max_tokens=1536,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return [s.model_dump() for s in result.steps][:5]
    except Exception as e:  # noqa: BLE001
        logger.warning("structured_plan failed (%s); caller will fall back", e)
        return None


def structured_review(
    *,
    task: str,
    plan_titles: List[str],
    files_touched: List[str],
    tests_passed: Optional[bool],
    workflow_id: Optional[str] = None,
) -> Optional[str]:
    """Return a summary string via instructor, or None on any error."""
    if not _structured_enabled():
        return None
    bundle = _build_models()
    if bundle is None:
        return None
    instructor = bundle["instructor"]
    litellm = bundle["litellm"]
    ReviewSummary = bundle["ReviewSummary"]

    system = (
        "Summarise the change in ≤60 words for a human reviewer about to click "
        "approve. State exactly what the diff does, which files moved, and one "
        "residual risk worth a second look — or 'no residual risk' if none. "
        "No emoji, no marketing, no recap of the plan."
    )
    user = (
        f"Task: {task}\n"
        f"Plan titles: {plan_titles}\n"
        f"Files touched: {files_touched}\n"
        f"Tests passed: {tests_passed}\n"
    )

    try:
        client = instructor.from_litellm(litellm.completion)
        result: "ReviewSummary" = client.messages.create(  # type: ignore[name-defined]
            model=_structured_model(),
            response_model=ReviewSummary,
            max_tokens=300,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return result.summary
    except Exception as e:  # noqa: BLE001
        logger.warning("structured_review failed (%s); caller will fall back", e)
        return None
