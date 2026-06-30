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

from .budget import BudgetExceeded, assert_within
from .state import CodeChange, GraphState, PlanStep, TestResult

logger = logging.getLogger(__name__)

# Single router instance per process; nodes inherit env-driven config.
_router = ModelRouter()


def _maybe_learned_prompt(task_type: str, fallback: str) -> str:
    """Return a tuned prompt from tuning/learned_prompts.json if
    `$USE_LEARNED_PROMPTS=1` and the file has an entry for this task_type.
    Otherwise return `fallback` (the canonical static prompt).

    Failure-resilient: any error returns `fallback` so a bad tune never
    silently breaks production runs.
    """
    import os as _os
    if _os.getenv("USE_LEARNED_PROMPTS", "").strip().lower() not in ("1", "true", "yes", "on"):
        return fallback
    try:
        from tuning import load_learned_prompts
        learned = load_learned_prompts()
    except Exception as _e:  # noqa: BLE001
        logger.warning("load_learned_prompts failed: %s", _e)
        return fallback
    return learned.get(task_type, fallback)


def _chat(task_type: str, system: str, user: str, *, workflow_id: str | None = None,
          state: Dict[str, Any] | None = None) -> str:
    # Phase U: refuse to spend if a budget ceiling is already past. We check
    # BEFORE the call rather than after so we never make the offending
    # request. The caller catches BudgetExceeded and routes to the budget
    # checkpoint.
    if state is not None:
        assert_within(state)
    elif workflow_id:
        assert_within({"workflow_id": workflow_id})
    messages: List[Message] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # If a LangGraph custom-stream writer is available, stream chunks through
    # it so the dashboard can render tokens live. Falls back to a single shot
    # if streaming or the writer isn't available.
    writer = _get_stream_writer()
    if writer is not None:
        try:
            text_parts: list[str] = []
            from router.base import ChatResult as _CR
            for item in _router.chat_stream(messages, task_type=task_type, workflow_id=workflow_id, max_tokens=2048):
                if isinstance(item, _CR):
                    return item.text
                text_parts.append(item)
                writer({"node": task_type, "chunk": item})
            return "".join(text_parts)
        except Exception as e:  # noqa: BLE001
            logger.warning("streaming failed (%s); falling back to non-streaming", e)
    res = _router.chat(messages, task_type=task_type, workflow_id=workflow_id, max_tokens=2048)
    return res.text


def _get_stream_writer():
    try:
        from langgraph.config import get_stream_writer
        return get_stream_writer()
    except Exception:
        return None


# ---------- nodes ----------


def _bb(exc: BudgetExceeded, next_node: str) -> Dict[str, Any]:
    """Build the state delta the graph routes on when a budget trips."""
    return {"budget_blocked": {**exc.to_dict(), "next": next_node}}


def analyze_node(state: GraphState) -> Dict[str, Any]:
    task = state["task"]
    wf = state.get("workflow_id")
    # Pull any prior-workflow memory that might be relevant.
    try:
        related = memory_store.search(task, limit=3)
    except Exception as e:  # storage hiccup shouldn't kill the workflow
        logger.warning("memory.search failed: %s", e)
        related = []
    extra_parts: list[str] = []
    if related:
        extra_parts.append("Related past notes:\n" + "\n---\n".join(d.text[:600] for d in related))
    # Pull a few vault hits too if Obsidian is configured.
    try:
        from storage import obsidian as _obs
        vault_hits = _obs.search_vault(task, limit=3)
    except Exception as e:  # noqa: BLE001
        logger.warning("obsidian.search_vault failed: %s", e)
        vault_hits = []
    if vault_hits:
        extra_parts.append("Related vault notes:\n" + "\n---\n".join(
            f"[{h['path']}] {h['snippet']}" for h in vault_hits
        ))
    # Phase R: if the task mentions a GitHub issue/PR (`#123` or URL), pull its
    # body via `gh` and inject as analyst context. Fails silently if `gh` is
    # missing or the ref doesn't resolve.
    try:
        from tools.git_ops import gh_context_for_task
        gh_block = gh_context_for_task(task)
    except Exception as e:  # noqa: BLE001
        logger.warning("gh_context_for_task failed: %s", e)
        gh_block = ""
    if gh_block:
        extra_parts.append("Related GitHub items:\n" + gh_block)
    extra = ("\n\n" + "\n\n".join(extra_parts)) if extra_parts else ""
    system = _maybe_learned_prompt("analyze", (
        "You are a senior engineer triaging a task before the planner sees it. "
        "Output <=100 words covering: (1) WHAT the user wants, (2) likely "
        "files/modules to touch, (3) the ONE risk that matters most. Skip "
        "filler like 'this is straightforward' or 'careful review needed'. "
        "If a risk is genuinely low, say so in 5 words and move on."
    ))
    try:
        analysis = _chat("analyze", system, f"Task: {task}{extra}", workflow_id=wf, state=state)
    except BudgetExceeded as e:
        return _bb(e, "do_analyze")
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
    "an array of step objects. Each object has `title` (<=80 chars, imperative "
    "voice — verb first), `detail` (1-2 sentences explaining WHY/HOW, not what "
    "the title already says), and `files` (relative paths the step will touch; "
    "[] if no file is touched yet).\n\n"
    "Step-count guidance:\n"
    "  - trivial 1-file edits (rename, docstring, comment, single-line fix): 1 step\n"
    "  - small features touching 1-2 files: 2-3 steps\n"
    "  - larger features: up to 5 steps\n"
    "  - hard cap: 5 steps\n\n"
    "FORBIDDEN steps (do NOT emit these — they are noise):\n"
    "  - 'Review requirements' / 'Identify approach' / 'Plan the implementation' (you ARE the plan)\n"
    "  - 'Open the file' / 'Locate the function' (the editor will do this)\n"
    "  - 'Verify accuracy' / 'Review for syntax errors' (the test step handles this)\n"
    "  - 'Commit changes' (the commit step handles this)\n\n"
    "Every step must actually change something concrete. No code, no prose, no markdown — JSON only."
)


def search_node(state: GraphState) -> Dict[str, Any]:
    """Phase V: optional pre-plan web search.

    Only runs when `state["search_enabled"]` is True. The conditional edge
    in graph.py also skips this node when the flag is unset, but we
    short-circuit here too so direct unit tests work.
    """
    if not state.get("search_enabled"):
        return {}
    task = state.get("task", "")
    if not task.strip():
        return {}
    try:
        from tools.web_search import search as web_search
    except Exception as e:  # noqa: BLE001
        logger.warning("web_search import failed: %s", e)
        return {}
    try:
        hits = web_search(task, k=5)
    except Exception as e:  # noqa: BLE001
        logger.warning("web_search failed: %s", e)
        hits = []
    return {"search_results": hits[:5]}


def _extract_json_array(text: str) -> list:
    # Best-effort: strip fences, find first `[`...`]`, then use json_repair
    # to fix common LLM JSON mistakes (trailing commas, unquoted keys, bad
    # escapes) before json.loads. Falls back to strict json.loads if
    # json_repair is unavailable.
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"No JSON array found in plan output: {text[:200]!r}")
    blob = text[start : end + 1]
    try:
        from json_repair import repair_json
        return json.loads(repair_json(blob))
    except ImportError:
        return json.loads(blob)


def plan_node(state: GraphState) -> Dict[str, Any]:
    wf = state.get("workflow_id")
    # Phase W: crew mode replaces the single-LLM plan with Planner+Critic.
    # The coordinator returns the same plan-step shape, so the downstream
    # code path is unchanged.
    try:
        from .crew import coordinate_plan, is_crew_mode
        crew_on = is_crew_mode(state)
    except Exception:  # noqa: BLE001
        crew_on = False
    if crew_on:
        try:
            assert_within(state)
            steps_raw = coordinate_plan(state["task"], state.get("analysis", ""), workflow_id=wf)
        except BudgetExceeded as e:
            return _bb(e, "do_plan")
        except Exception as e:  # noqa: BLE001
            logger.warning("crew coordinator failed (%s); falling back to single-LLM plan", e)
            steps_raw = None
        if steps_raw is not None:
            steps: List[PlanStep] = []
            for s in steps_raw[:5]:
                if not isinstance(s, dict):
                    continue
                steps.append(PlanStep(
                    title=str(s.get("title", ""))[:200],
                    detail=str(s.get("detail", "")),
                    files=[str(f) for f in (s.get("files") or [])],
                ))
            return {"plan": steps}

    user = f"Task: {state['task']}\n\nContext from analysis:\n{state.get('analysis', '')}"
    # Phase V: prepend short web-search context block when search ran.
    hits = state.get("search_results") or []
    search_block = ""
    if hits:
        lines = ["Recent web context (top results, trimmed):"]
        for i, h in enumerate(hits, 1):
            title = (h.get("title") or "")[:120]
            snippet = (h.get("snippet") or "").replace("\n", " ")[:200]
            url = h.get("url", "")
            lines.append(f"  {i}. {title} — {snippet} ({url})")
        search_block = "\n".join(lines)
        user = search_block + "\n\n" + user

    # Phase Z2: instructor + pydantic structured output. Opt-in via
    # $USE_STRUCTURED_OUTPUTS=1. Returns None and falls through if disabled,
    # deps missing, or the LLM call fails — so behavior is unchanged for
    # users who don't flip the flag.
    try:
        from .structured import structured_plan
        struct_steps = structured_plan(
            task=state["task"],
            analysis=state.get("analysis", ""),
            search_block=search_block,
            workflow_id=wf,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("structured_plan import/call failed: %s", e)
        struct_steps = None
    if struct_steps is not None:
        steps: List[PlanStep] = []
        for s in struct_steps[:5]:
            steps.append(PlanStep(
                title=str(s.get("title", ""))[:200],
                detail=str(s.get("detail", "")),
                files=[str(f) for f in (s.get("files") or [])],
            ))
        return {"plan": steps}

    try:
        raw = _chat("plan", _maybe_learned_prompt("plan", _PLAN_SYSTEM), user, workflow_id=wf, state=state)
    except BudgetExceeded as e:
        return _bb(e, "do_plan")
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

    try:
        assert_within(state)
        changes = apply_plan(
            task=state["task"],
            analysis=state.get("analysis", ""),
            plan=state.get("plan", []),
            workflow_id=state.get("workflow_id"),
        )
    except BudgetExceeded as e:
        return _bb(e, "do_code")
    return {"code_changes": list(changes)}


def test_node(state: GraphState) -> Dict[str, Any]:
    # SECURITY: pytest runs in the current working directory, which is the
    # project root. Workflows that touch files outside a controlled sandbox
    # risk executing arbitrary test code. Restrict via WORKFLOW_TEST_CWD env
    # or by sandboxing the container. Timeout is bounded by the env var
    # WORKFLOW_TEST_TIMEOUT_S (default 300s).
    import os as _os
    _cwd = _os.getenv("WORKFLOW_TEST_CWD") or None
    _timeout = int(_os.getenv("WORKFLOW_TEST_TIMEOUT_S", "300"))
    cmd = ["python", "-m", "pytest", "-x", "--tb=short", "-q"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=_timeout, cwd=_cwd)
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
    # Crew mode: Tester + Reviewer crew gives two perspectives merged into the
    # final summary. Falls back to single-LLM summary if crew is off or fails.
    try:
        from .crew import coordinate_review, is_crew_mode
        crew_on = is_crew_mode(state)
    except Exception:  # noqa: BLE001
        crew_on = False
    summary: str
    if crew_on:
        try:
            summary = coordinate_review(
                task=state.get("task") or "",
                plan=state.get("plan") or [],
                code_changes=state.get("code_changes") or [],
                test_results=state.get("test_results"),
                workflow_id=wf,
            )
        except BudgetExceeded as e:
            return _bb(e, "do_review")
        except Exception as e:  # noqa: BLE001
            logger.warning("crew review failed (%s); falling back to single-LLM", e)
            crew_on = False
    if not crew_on:
        plan_titles = [s.get("title") for s in state.get("plan", [])]
        files_touched = sorted({c.get("path", "") for c in state.get("code_changes", [])})
        tests_passed = (state.get("test_results") or {}).get("passed")

        # Phase Z2: try structured review via instructor first.
        try:
            from .structured import structured_review
            struct = structured_review(
                task=state.get("task") or "",
                plan_titles=[t for t in plan_titles if t],
                files_touched=files_touched,
                tests_passed=tests_passed,
                workflow_id=wf,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("structured_review import/call failed: %s", e)
            struct = None
        if struct is not None:
            summary = struct
            try:
                memory_store.add(
                    f"TASK: {state.get('task')}\nSUMMARY: {summary}",
                    kind="review", workflow_id=wf,
                    meta={"files": files_touched},
                )
            except Exception as e:
                logger.warning("memory.add(review) failed: %s", e)
            return {"review_summary": summary}

        summary_input = json.dumps(
            {
                "task": state.get("task"),
                "plan_titles": plan_titles,
                "files_touched": files_touched,
                "tests_passed": tests_passed,
            },
            indent=2,
        )
        system = _maybe_learned_prompt("review", (
            "Summarise the change in <=60 words for a human reviewer about to "
            "click approve. State exactly what the diff does, which files moved, "
            "and one residual risk worth a second look — or 'no residual risk' "
            "if genuinely none. No emoji, no marketing, no recap of the plan."
        ))
        try:
            summary = _chat("review", system, summary_input, workflow_id=wf, state=state)
        except BudgetExceeded as e:
            return _bb(e, "do_review")
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
    paths = [c["path"] for c in state.get("code_changes", []) if c.get("path")] or None
    try:
        sha = commit_changes(message=msg, paths=paths)
    except Exception as exc:
        logger.error("commit_node failed: %s", exc, exc_info=True)
        return {"error": str(exc), "commit_sha": None, "commit_message": msg}
    return {"commit_sha": sha, "commit_message": msg}


def retrospective_node(state: GraphState) -> Dict[str, Any]:
    """Phase Z: capture outcome facts + LLM self-critique.

    Runs after `do_commit`. Writes one row to `storage.retrospectives` with
    task, verdict, plan_steps, test_retries, files_touched, cost_usd,
    crew_mode, commit_sha, and an optional LLM-generated critique. Never
    raises — retrospective failures must not break a green workflow.
    """
    from storage import retrospectives as retro_store
    from storage import accounting

    wf = state.get("workflow_id") or ""
    task = state.get("task") or ""
    tr = state.get("test_results") or {}
    tests_passed = tr.get("passed") if isinstance(tr, dict) else None
    code_changes = state.get("code_changes") or []
    files_touched = len({c.get("path", "") for c in code_changes if c.get("path")})
    plan_steps = len(state.get("plan") or [])
    commit_sha = state.get("commit_sha")
    retries = int(state.get("retries") or 0)

    if commit_sha:
        verdict = "success"
    elif state.get("budget_blocked"):
        verdict = "budget_abort"
    elif state.get("approved") is False:
        verdict = "rejected_commit"
    elif tests_passed is False:
        verdict = "tests_failed"
    else:
        verdict = "error"

    try:
        cost = accounting.workflow_cost(wf) if wf else 0.0
    except Exception as e:  # noqa: BLE001
        logger.warning("retrospective accounting.workflow_cost failed: %s", e)
        cost = 0.0

    try:
        from .crew import is_crew_mode
        crew_on = is_crew_mode(state)
    except Exception:  # noqa: BLE001
        crew_on = False

    notes = ""
    # Self-critique LLM call is best-effort and budget-gated.
    try:
        assert_within(state)
        critique_input = (
            f"Task: {task}\n"
            f"Verdict: {verdict}\n"
            f"Plan steps: {plan_steps}\n"
            f"Test retries: {retries}\n"
            f"Tests passed: {tests_passed}\n"
            f"Files touched: {files_touched}\n"
        )
        system = (
            "You are a retrospective critic. In <=40 words, name ONE thing "
            "that would have made this workflow cheaper, faster, or more "
            "correct next time. Be specific. If nothing, say 'no improvement'."
        )
        notes = _chat("review", system, critique_input, workflow_id=wf, state=state)
    except BudgetExceeded:
        notes = "(critique skipped — budget exhausted)"
    except Exception as e:  # noqa: BLE001
        logger.warning("retrospective critique failed: %s", e)
        notes = ""

    try:
        retro_store.record(
            workflow_id=wf, task=task, verdict=verdict,
            plan_steps=plan_steps, test_retries=retries,
            tests_passed=tests_passed, files_touched=files_touched,
            cost_usd=cost, crew_mode=crew_on, commit_sha=commit_sha,
            notes=notes,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("retrospective.record failed: %s", e)

    try:
        memory_store.add(
            f"TASK: {task}\nVERDICT: {verdict}\nNOTES: {notes}",
            kind="retrospective",
            workflow_id=wf,
            meta={"verdict": verdict, "retries": retries, "cost_usd": cost},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("memory.add(retrospective) failed: %s", e)

    return {"retrospective": {"verdict": verdict, "notes": notes, "cost_usd": cost}}


def _build_commit_message(state: GraphState) -> str:
    plan_titles = [s.get("title", "") for s in state.get("plan", [])]
    head = (state.get("task") or "AI Company change").splitlines()[0][:72]
    body_lines = ["", *(f"- {t}" for t in plan_titles if t)]
    return head + ("\n".join(body_lines) if len(body_lines) > 1 else "")
