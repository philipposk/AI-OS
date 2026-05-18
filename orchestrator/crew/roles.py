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
