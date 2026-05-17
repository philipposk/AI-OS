"""Apply a plan to the working tree.

Provider-agnostic: asks the router for a structured JSON response of
{changes:[{path,content}]} where `content` is the FULL new file body. We then
diff against disk, write, and return CodeChange records for the review
checkpoint.

Why not Anthropic tool-use specifically? The orchestrator must work with
OpenRouter / NVIDIA / Groq / Ollama too. A single structured-JSON contract is
the lowest common denominator and is robust enough with one repair pass when
the model emits malformed JSON.
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
from pathlib import Path
from typing import Iterable, List

from router import ModelRouter
from router.base import Message

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 200_000  # don't shove huge files into the prompt
MAX_FILES_PER_CALL = 12
WORKING_TREE = Path(os.getenv("AI_COMPANY_REPO_PATH", ".")).resolve()

_SYSTEM = """You are a careful coding agent applying a small, reviewable change.

You will receive:
- task: the user's request
- analysis: prior analysis of the task
- plan: ordered list of steps with `title`, `detail`, `files`
- current_files: dict of {path: current_contents} for every file in the plan

Respond with STRICT JSON ONLY, no markdown fences, no commentary:

{
  "changes": [
    { "path": "<relative path>", "content": "<entire new file body>" }
  ]
}

Rules:
- `content` must be the COMPLETE new file body, not a diff or snippet.
- Do not change files not in the plan unless absolutely necessary; if you must,
  add them with a brief reason in a top-level "notes" string.
- Preserve existing style (indentation width, quote style, trailing newline).
- If a file should be deleted, set "content" to null.
- If you cannot complete the change, return {"changes": []} and explain in "notes".
"""

_router = ModelRouter()


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"{path} is {path.stat().st_size} bytes; exceeds MAX_FILE_BYTES")
    return path.read_text(encoding="utf-8")


def _diff(path: str, before: str, after: str | None) -> str:
    a = before.splitlines(keepends=True)
    b = (after or "").splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=f"a/{path}", tofile=f"b/{path}", n=3))


def _collect_files(plan: List[dict]) -> List[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for step in plan or []:
        for f in step.get("files", []) or []:
            if f and f not in seen:
                seen.add(f)
                paths.append(f)
    return paths


def _build_messages(task: str, analysis: str, plan: list, current_files: dict[str, str]) -> List[Message]:
    user = json.dumps(
        {
            "task": task,
            "analysis": analysis,
            "plan": plan,
            "current_files": current_files,
        },
        indent=2,
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(text)
        if not m:
            raise
        return json.loads(m.group(0))


def _ask_for_changes(messages: List[Message], workflow_id: str | None) -> dict:
    res = _router.chat(
        messages,
        task_type="code",
        max_tokens=4096,
        temperature=0.2,
        workflow_id=workflow_id,
    )
    try:
        return _parse_json_object(res.text)
    except Exception as e:
        logger.warning("JSON parse failed (%s); requesting one repair", e)
        repair_msgs = messages + [
            {"role": "assistant", "content": res.text},
            {
                "role": "user",
                "content": (
                    "Your previous reply was not valid JSON. Reply now with ONLY the JSON object "
                    "described in the system prompt — no markdown, no commentary."
                ),
            },
        ]
        res2 = _router.chat(
            repair_msgs,
            task_type="code",
            max_tokens=4096,
            temperature=0.0,
            workflow_id=workflow_id,
        )
        return _parse_json_object(res2.text)


def apply_plan(
    *,
    task: str,
    analysis: str,
    plan: List[dict],
    workflow_id: str | None = None,
    dry_run: bool = False,
) -> Iterable[dict]:
    paths = _collect_files(plan)
    if not paths:
        logger.info("apply_plan: plan has no files; nothing to do")
        return []
    if len(paths) > MAX_FILES_PER_CALL:
        raise ValueError(
            f"plan touches {len(paths)} files; max {MAX_FILES_PER_CALL} per call. "
            "Split the plan into smaller steps."
        )

    current: dict[str, str] = {}
    for p in paths:
        current[p] = _read(WORKING_TREE / p)

    messages = _build_messages(task, analysis, plan, current)
    response = _ask_for_changes(messages, workflow_id)
    notes = response.get("notes") or ""
    if notes:
        logger.info("file_editor notes: %s", notes)
    changes_raw = response.get("changes") or []

    code_changes: list[dict] = []
    for ch in changes_raw:
        if not isinstance(ch, dict):
            continue
        path = ch.get("path")
        if not path:
            continue
        new_content = ch.get("content")  # may be None → delete
        full_path = (WORKING_TREE / path).resolve()
        try:
            full_path.relative_to(WORKING_TREE)
        except ValueError:
            logger.warning("rejected change outside working tree: %s", path)
            continue
        before = current.get(path, _read(full_path))
        after = "" if new_content is None else str(new_content)
        diff_text = _diff(path, before, after) if new_content is not None else _diff(path, before, None)

        if not dry_run:
            if new_content is None:
                if full_path.exists():
                    full_path.unlink()
            else:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                full_path.write_text(after, encoding="utf-8")

        code_changes.append(
            {
                "path": path,
                "before": before,
                "after": after if new_content is not None else None,
                "diff": diff_text,
            }
        )

    return code_changes
