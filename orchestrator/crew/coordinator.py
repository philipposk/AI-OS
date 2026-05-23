"""Crew coordinator — Planner + Critic → reconciled plan.

Activated by `is_crew_mode(state)`. Returns the same JSON-array shape as
`nodes.plan_node` so it's a drop-in replacement.

Flow:
    1. Planner drafts JSON plan.
    2. Critic reviews the draft (parallel-friendly — runs alongside planner
       only on demand; sequential is fine for first cut to keep determinism
       and avoid doubling LLM cost when the draft is rejected outright).
    3. Planner revises ONCE incorporating Critic's notes; final JSON wins.

Every step posts to the bus so the dashboard's transcript reflects the
deliberation.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from .bus import get_bus
from .roles import Critic, OpenHandsRole, Planner, Reviewer, Tester

logger = logging.getLogger(__name__)


def is_crew_mode(state: Optional[Dict[str, Any]] = None) -> bool:
    """state override beats env."""
    if isinstance(state, dict) and state.get("crew_mode") is not None:
        return bool(state["crew_mode"])
    return os.getenv("CREW_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def _extract_json_array(text: str) -> List[dict]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON array in role output")
    blob = text[start:end + 1]
    try:
        from json_repair import repair_json
        return json.loads(repair_json(blob))
    except ImportError:
        return json.loads(blob)


def coordinate_plan(task: str, analysis: str, *, workflow_id: Optional[str] = None) -> List[dict]:
    """Returns plan steps. Posts every role response to the bus."""
    bus = get_bus()
    wf = workflow_id or ""

    planner = Planner()
    critic = Critic()

    bus.post(workflow_id=wf, role="coordinator", content=f"start crew for task: {task!r}")

    draft = planner.respond(
        f"Task: {task}\n\nAnalysis context:\n{analysis}",
        workflow_id=wf, max_tokens=1536, temperature=0.4,
    )
    bus.post(workflow_id=wf, role="planner", content=draft.content,
             meta={"model": draft.model, "provider": draft.provider,
                   "in": draft.prompt_tokens, "out": draft.completion_tokens})

    try:
        draft_steps = _extract_json_array(draft.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("planner draft not parseable (%s); using one-step fallback", e)
        draft_steps = [{"title": task[:80], "detail": draft.content[:240], "files": []}]

    critique = critic.respond(
        "Draft plan (JSON):\n" + json.dumps(draft_steps, indent=2) +
        f"\n\nTask:\n{task}\n\nAnalysis:\n{analysis}",
        workflow_id=wf, max_tokens=512, temperature=0.4,
    )
    bus.post(workflow_id=wf, role="critic", content=critique.content,
             meta={"model": critique.model, "provider": critique.provider,
                   "in": critique.prompt_tokens, "out": critique.completion_tokens})

    revised = planner.respond(
        "Revise the plan below to address every point in the critique. Output "
        "STRICT JSON only.\n\nDraft:\n" + json.dumps(draft_steps, indent=2) +
        "\n\nCritique:\n" + critique.content +
        f"\n\nTask:\n{task}",
        workflow_id=wf, max_tokens=1536, temperature=0.3,
    )
    bus.post(workflow_id=wf, role="planner", content=revised.content,
             meta={"model": revised.model, "provider": revised.provider,
                   "revision": True,
                   "in": revised.prompt_tokens, "out": revised.completion_tokens})

    try:
        final_steps = _extract_json_array(revised.content)
    except Exception as e:  # noqa: BLE001
        logger.warning("planner revision not parseable (%s); keeping draft", e)
        final_steps = draft_steps

    bus.post(workflow_id=wf, role="coordinator",
             content=f"final plan: {len(final_steps)} step(s)")
    return final_steps


def coordinate_code(
    task: str,
    analysis: str,
    plan: List[dict],
    *,
    workflow_id: Optional[str] = None,
) -> str:
    """Delegate coding to OpenHands if enabled, else fall back to Coder role.

    Returns a freeform string (diff summary or file-map JSON). The caller
    (`nodes.code_node`) converts it to `CodeChange` objects.
    """
    bus = get_bus()
    wf = workflow_id or ""

    coder = OpenHandsRole()  # falls back to Coder internally when OH disabled
    plan_text = json.dumps(plan, indent=2)
    result = coder.respond(
        f"Task: {task}\n\nAnalysis:\n{analysis}\n\nPlan:\n{plan_text}",
        workflow_id=wf, max_tokens=2048, temperature=0.3,
    )
    bus.post(workflow_id=wf, role=result.role, content=result.content[:800],
             meta={"model": result.model, "provider": result.provider,
                   "in": result.prompt_tokens, "out": result.completion_tokens})
    return result.content


def coordinate_review(
    *,
    task: str,
    plan: List[dict],
    code_changes: List[dict],
    test_results: Optional[Dict[str, Any]] = None,
    workflow_id: Optional[str] = None,
) -> str:
    """Reviewer + Tester perspectives merged into one summary string.

    Tester first calls out missing test coverage or risky edges; Reviewer
    writes the final human-facing summary that incorporates the Tester's
    notes. Every role response is bussed for the dashboard transcript.
    """
    bus = get_bus()
    wf = workflow_id or ""

    plan_titles = [s.get("title", "") for s in (plan or [])]
    files = sorted({c.get("path", "") for c in (code_changes or []) if c.get("path")})
    tests_passed = (test_results or {}).get("passed")
    tr_stdout = ((test_results or {}).get("stdout") or "")[-800:]

    tester = Tester()
    tester_input = (
        f"Task: {task}\n"
        f"Plan titles: {plan_titles}\n"
        f"Files changed: {files}\n"
        f"Tests passed: {tests_passed}\n"
        f"Test output (tail):\n{tr_stdout}"
    )
    t_res = tester.respond(tester_input, workflow_id=wf, max_tokens=400, temperature=0.4)
    bus.post(workflow_id=wf, role="tester", content=t_res.content,
             meta={"model": t_res.model, "provider": t_res.provider,
                   "in": t_res.prompt_tokens, "out": t_res.completion_tokens})

    reviewer = Reviewer()
    reviewer_input = (
        f"Task: {task}\n"
        f"Plan titles: {plan_titles}\n"
        f"Files changed: {files}\n"
        f"Tests passed: {tests_passed}\n\n"
        f"Tester notes:\n{t_res.content}"
    )
    r_res = reviewer.respond(reviewer_input, workflow_id=wf, max_tokens=300, temperature=0.3)
    bus.post(workflow_id=wf, role="reviewer", content=r_res.content,
             meta={"model": r_res.model, "provider": r_res.provider,
                   "in": r_res.prompt_tokens, "out": r_res.completion_tokens})

    bus.post(workflow_id=wf, role="coordinator", content="review crew done")
    return r_res.content
