"""LangGraph state schema."""
from __future__ import annotations

from typing import Annotated, Any, List, Optional, TypedDict

from langgraph.graph.message import add_messages


class TestResult(TypedDict, total=False):
    returncode: int
    stdout: str
    stderr: str
    passed: bool


class PlanStep(TypedDict, total=False):
    title: str
    detail: str
    files: List[str]


class CodeChange(TypedDict, total=False):
    path: str
    before: str
    after: str
    diff: str


class GraphState(TypedDict, total=False):
    """End-to-end orchestrator state.

    Keys are optional because nodes incrementally populate them. Each node
    returns a dict with just its own writes; LangGraph merges into state.
    """

    # Input
    workflow_id: str
    task: str

    # Node outputs
    analysis: str
    plan: List[PlanStep]
    code_changes: List[CodeChange]
    test_results: TestResult
    review_summary: str
    commit_sha: Optional[str]
    commit_message: str

    # Control flow
    retries: int
    approved: Optional[bool]  # set by human checkpoint
    rejection_reason: Optional[str]

    # Debug / observability
    history: Annotated[list, add_messages]
    error: Optional[str]
