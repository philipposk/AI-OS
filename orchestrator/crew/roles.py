"""Crew roles — thin specialists over ModelRouter.

Each role has:
  * a fixed system prompt slanted for that perspective
  * its own task_type so the router can map it to a provider/model
  * an optional `$CREW_<ROLE>_MODEL` env override that overrides the
    task_type's normal mapping for *just this role* (we set
    `ROUTER_MODEL_<TASK_TYPE>` for the duration of the call).

The roles do not own the bus — the coordinator posts on their behalf so
ordering, parent-message linking, and meta stay in one place.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from router import ModelRouter
from router.base import Message


@dataclass
class RoleResult:
    role: str
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@contextmanager
def _temp_env(name: str, value: Optional[str]):
    """Set `name` for the duration of the block; restore prior on exit."""
    prev = os.environ.get(name)
    if value:
        os.environ[name] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = prev


class Role:
    name: str = "role"
    task_type: str = "simple"
    system: str = ""

    def __init__(self, router: Optional[ModelRouter] = None) -> None:
        self._router = router or ModelRouter()

    def _model_env_var(self) -> str:
        return f"CREW_{self.name.upper()}_MODEL"

    def _task_env_var(self) -> str:
        return f"ROUTER_MODEL_{self.task_type.upper()}"

    def respond(self, user: str, *, workflow_id: Optional[str] = None,
                max_tokens: int = 1024, temperature: float = 0.4) -> RoleResult:
        msgs: list[Message] = [
            {"role": "system", "content": self.system},
            {"role": "user", "content": user},
        ]
        override = os.environ.get(self._model_env_var())
        with _temp_env(self._task_env_var(), override):
            res = self._router.chat(
                msgs, task_type=self.task_type,
                workflow_id=workflow_id,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        return RoleResult(
            role=self.name,
            content=res.text,
            model=res.model,
            provider=res.provider,
            prompt_tokens=res.prompt_tokens,
            completion_tokens=res.completion_tokens,
        )


class Planner(Role):
    name = "planner"
    task_type = "plan"
    system = (
        "You are the Planner. Output STRICT JSON only — an array of 1-5 step "
        "objects with keys `title` (<=80 chars), `detail` (1-2 sentences), and "
        "`files` (relative paths the step will touch). No commentary."
    )


class Critic(Role):
    name = "critic"
    task_type = "review"
    system = (
        "You are the Critic. Review a draft plan and list its weakest points "
        "in <=120 words: missing edge cases, scope creep, files likely to break, "
        "tests that should be added. Be specific. No emoji."
    )


class Coder(Role):
    name = "coder"
    task_type = "code"
    system = (
        "You are the Coder. Produce the minimum diff that fulfills the plan. "
        "Output STRICT JSON only — an object mapping relative file paths to "
        "their new full contents."
    )


class Tester(Role):
    name = "tester"
    task_type = "review"
    system = (
        "You are the Tester. Given a plan and a diff, list (1) which tests "
        "should change, (2) which new tests should exist, and (3) any edge "
        "case the diff might miss. <=120 words. No code."
    )


class Reviewer(Role):
    name = "reviewer"
    task_type = "review"
    system = (
        "You are the Reviewer. Summarise the change in <=80 words for a human "
        "approver: what changed, which files, whether tests passed, residual risk."
    )


class OpenHandsRole(Role):
    """Delegate coding tasks to a running OpenHands (All-Hands-AI) instance.

    When `CREW_OPENHANDS=true` and `OPENHANDS_API_URL` is set (default
    http://localhost:3000), this role sends the task + plan to OpenHands via
    its REST API, polls until done, and returns the resulting diff/summary.

    Falls back to the ordinary `Coder` role if OpenHands is unreachable or
    the env flags are off.

    Required env:
        OPENHANDS_API_URL   — base URL of local OpenHands server (default http://localhost:3000)
        OPENHANDS_API_KEY   — bearer token if OpenHands auth is enabled (optional)
        CREW_OPENHANDS      — '1' / 'true' / 'yes' / 'on' to enable
    """

    name = "openhands"
    task_type = "code"
    _POLL_INTERVAL = 5   # seconds
    _POLL_TIMEOUT = 300  # seconds

    @staticmethod
    def enabled() -> bool:
        return os.getenv("CREW_OPENHANDS", "").strip().lower() in ("1", "true", "yes", "on")

    @staticmethod
    def _base_url() -> str:
        return os.getenv("OPENHANDS_API_URL", "http://localhost:3000").rstrip("/")

    @staticmethod
    def _headers() -> dict:
        headers = {"Content-Type": "application/json"}
        key = os.getenv("OPENHANDS_API_KEY", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def respond(self, user: str, *, workflow_id: Optional[str] = None,
                max_tokens: int = 1024, temperature: float = 0.4) -> RoleResult:
        """Try OpenHands first; fall back to Coder role on any failure."""
        if not self.enabled():
            return Coder(self._router).respond(user, workflow_id=workflow_id,
                                               max_tokens=max_tokens, temperature=temperature)
        try:
            return self._call_openhands(user, workflow_id=workflow_id)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "OpenHands unreachable (%s); falling back to Coder role", exc
            )
            return Coder(self._router).respond(user, workflow_id=workflow_id,
                                               max_tokens=max_tokens, temperature=temperature)

    def _call_openhands(self, task: str, *, workflow_id: Optional[str]) -> RoleResult:
        import time
        import urllib.request

        base = self._base_url()
        headers = self._headers()

        # 1. Create a conversation / run.
        import json as _json
        body = _json.dumps({"task": task, "workflow_id": workflow_id or ""}).encode()
        req = urllib.request.Request(f"{base}/api/conversations",
                                     data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            conv = _json.loads(resp.read())
        conv_id = conv.get("conversation_id") or conv.get("id")
        if not conv_id:
            raise RuntimeError(f"OpenHands: no conversation_id in response: {conv}")

        # 2. Poll until status ∈ {finished, error, stopped}.
        deadline = time.time() + self._POLL_TIMEOUT
        result_text = ""
        while time.time() < deadline:
            time.sleep(self._POLL_INTERVAL)
            req2 = urllib.request.Request(
                f"{base}/api/conversations/{conv_id}",
                headers=headers, method="GET",
            )
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                status_data = _json.loads(resp2.read())
            status = (status_data.get("status") or "").lower()
            if status in ("finished", "error", "stopped"):
                result_text = status_data.get("result") or status_data.get("message") or status
                break

        if not result_text:
            result_text = f"OpenHands timed out after {self._POLL_TIMEOUT}s for conv {conv_id}"

        return RoleResult(
            role=self.name,
            content=result_text,
            model="openhands",
            provider="openhands",
            prompt_tokens=0,
            completion_tokens=0,
        )
