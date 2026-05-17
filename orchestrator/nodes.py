"""Graph nodes — each takes GraphState, returns a partial dict to merge."""
from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any, Dict, List

from router import ModelRouter
from router.base import Message
from storage import memory as memory_store

from .state import CodeChange, GraphState, PlanStep, TestResult

logger = logging.getLogger(__name__)

# Single router instance per process; nodes inherit env-driven config.
_router = ModelRouter()


def _chat(task_type: str, system: str, user: str, *, workflow_id: str | None = None) -> str:
    messages: List[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    res = _router.chat(messages, task_type=task_type, workflow_id=workflow_id, max_tokens=2048)
    return res.text


# ---------- nodes ----------


def analyze_node(state: GraphState) -> Dict[str, Any]:
    task = state["task"]
    wf = state.get("workflow_id")
    # Pull any prior-workflow memory that might be relevant.
    try:
        related = memory_store.search(task, limit=3)
    except Exception as e:  # storage hiccup shouldn't kill the workflow
        logger.warning("memory.search failed: %s", e)
        related = []
    extra = ""
    if related:
        extra = "\n\nRelated past notes (most relevant first):\n" + "\n---\n".join(
            d.text[:600] for d in related
        )
    system = (
        "You are a senior engineer breaking down an incoming task. In <=120 words, "
        "describe what the user is trying to accomplish, the likely files/areas involved, "
        "and the most important risks or unknowns. Be specific."
    )
    analysis = _chat("analyze", system, f"Task: {task}{extra}", workflow_id=wf)
    try:
        memory_store.add(
            f"TASK: {task}\nANALYSIS: {analysis}",
            kind="analysis",
            workflow_id=wf,
            meta={"task": task},
        )
    except Exception as e:
        logger.warning("memory.add(analysis) failed: %s", e)
    return {"analysis": analysis, "retries": 0}


_PLAN_SYSTEM = (
    "You are planning a small, reviewable code change. Output STRICT JSON only — "
    "an array of 1-5 step objects. Each object has `title` (<=80 chars), `detail` "
    "(1-2 sentences), and `files` (array of relative paths the step will touch; "
    "empty if no file is touched yet). Do not include code, prose, or markdown."
)


def _extract_json_array(text: str) -> list:
    # Best-effort: strip fences, find first `[`...`]`.
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in plan output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def plan_node(state: GraphState) -> Dict[str, Any]:
    wf = state.get("workflow_id")
    user = f"Task: {state['task']}\n\nContext from analysis:\n{state.get('analysis', '')}"
    raw = _chat("plan", _PLAN_SYSTEM, user, workflow_id=wf)
    try:
        steps_raw = _extract_json_array(raw)
    except Exception as e:
        logger.warning("plan_node JSON parse failed (%s), falling back to single step", e)
        steps_raw = [{"title": state["task"][:80], "detail": raw[:240], "files": []}]
    steps: List[PlanStep] = []
    for s in steps_raw[:5]:
        if not isinstance(s, dict):
            continue
        steps.append(
            PlanStep(
                title=str(s.get("title", ""))[:200],
                detail=str(s.get("detail", "")),
                files=[str(f) for f in (s.get("files") or [])],
            )
        )
    return {"plan": steps}


def code_node(state: GraphState) -> Dict[str, Any]:
    """Apply the plan with the file_editor tool.

    Phase D wires the real Anthropic tool-use editor. For Phase C we record a
    placeholder change so downstream nodes (test / review / commit) can be
    exercised end-to-end with a mock router in tests.
    """
    try:
        from tools.file_editor import apply_plan  # provided in Phase D
    except ImportError:
        logger.info("file_editor not yet available (Phase D pending); recording stub change")
        changes: List[CodeChange] = []
        for step in state.get("plan", []):
            for f in step.get("files", []) or []:
                changes.append(CodeChange(path=f, before="", after="", diff=f"# stub change for {f}"))
        return {"code_changes": changes}

    changes = apply_plan(
        task=state["task"],
        analysis=state.get("analysis", ""),
        plan=state.get("plan", []),
        workflow_id=state.get("workflow_id"),
    )
    return {"code_changes": list(changes)}


def test_node(state: GraphState) -> Dict[str, Any]:
    cmd = ["python", "-m", "pytest", "-x", "--tb=short", "-q"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"test_results": TestResult(returncode=124, stdout="", stderr="timeout", passed=False)}
    except FileNotFoundError:
        return {"test_results": TestResult(returncode=127, stdout="", stderr="pytest not installed", passed=False)}
    return {
        "test_results": TestResult(
            returncode=res.returncode,
            stdout=res.stdout[-4000:],
            stderr=res.stderr[-2000:],
            passed=res.returncode == 0,
        )
    }


def review_node(state: GraphState) -> Dict[str, Any]:
    wf = state.get("workflow_id")
    summary_input = json.dumps(
        {
            "task": state.get("task"),
            "plan_titles": [s.get("title") for s in state.get("plan", [])],
            "files_touched": sorted({c.get("path", "") for c in state.get("code_changes", [])}),
            "tests_passed": (state.get("test_results") or {}).get("passed"),
        },
        indent=2,
    )
    system = (
        "Summarise the change in <=80 words for a human reviewer. State what changed, "
        "which files were touched, whether tests passed, and any risk worth a second look. "
        "No emoji, no marketing tone."
    )
    summary = _chat("review", system, summary_input, workflow_id=wf)
    try:
        memory_store.add(
            f"TASK: {state.get('task')}\nSUMMARY: {summary}",
            kind="review",
            workflow_id=wf,
            meta={"files": sorted({c.get('path', '') for c in state.get('code_changes', [])})},
        )
    except Exception as e:
        logger.warning("memory.add(review) failed: %s", e)
    return {"review_summary": summary}


def commit_node(state: GraphState) -> Dict[str, Any]:
    from tools.git_ops import commit_changes  # provided in Phase D

    msg = state.get("commit_message") or _build_commit_message(state)
    sha = commit_changes(message=msg, paths=[c["path"] for c in state.get("code_changes", []) if c.get("path")])
    return {"commit_sha": sha, "commit_message": msg}


def _build_commit_message(state: GraphState) -> str:
    plan_titles = [s.get("title", "") for s in state.get("plan", [])]
    head = (state.get("task") or "AI Company change").splitlines()[0][:72]
    body_lines = ["", *(f"- {t}" for t in plan_titles if t)]
    return head + ("\n".join(body_lines) if len(body_lines) > 1 else "")
