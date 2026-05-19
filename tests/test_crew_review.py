"""Crew review-step: Tester + Reviewer perspectives merged. No network."""
from __future__ import annotations

import pytest

import orchestrator.crew.coordinator as coord
import orchestrator.crew.roles as roles_mod
import orchestrator.nodes as nodes_mod
from orchestrator.crew.roles import RoleResult


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "crewrev.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")


def _stub_role_responses(monkeypatch, *, tester_text: str, reviewer_text: str):
    """Patch Role.respond so Tester returns tester_text and Reviewer returns reviewer_text."""
    def fake_respond(self, user, *, workflow_id=None, max_tokens=1024, temperature=0.4):
        if self.name == "tester":
            text = tester_text
        elif self.name == "reviewer":
            text = reviewer_text
        else:
            text = "ok"
        return RoleResult(
            role=self.name, content=text, model="stub", provider="stub",
            prompt_tokens=1, completion_tokens=1,
        )
    monkeypatch.setattr(roles_mod.Role, "respond", fake_respond)


def test_coordinate_review_returns_reviewer_text(monkeypatch):
    _stub_role_responses(monkeypatch,
                         tester_text="add edge case for empty input",
                         reviewer_text="Added --version flag. cli.py touched. Tests pass.")
    out = coord.coordinate_review(
        task="add --version",
        plan=[{"title": "Add flag", "files": ["cli.py"]}],
        code_changes=[{"path": "cli.py"}],
        test_results={"passed": True},
        workflow_id="wf-1",
    )
    assert "Added --version" in out


def test_coordinate_review_posts_both_roles_to_bus(monkeypatch):
    _stub_role_responses(monkeypatch,
                         tester_text="missing tests for negative path",
                         reviewer_text="summary text here")
    from orchestrator.crew.bus import get_bus
    bus = get_bus()
    coord.coordinate_review(
        task="x",
        plan=[],
        code_changes=[{"path": "a.py"}],
        test_results={"passed": False, "stdout": "trace"},
        workflow_id="wf-2",
    )
    msgs = bus.transcript("wf-2")
    roles_seen = [m.role for m in msgs]
    assert "tester" in roles_seen
    assert "reviewer" in roles_seen
    # Coordinator marks done
    assert any(m.role == "coordinator" and "review crew done" in m.content for m in msgs)


def test_review_node_uses_crew_when_crew_mode_on(monkeypatch):
    monkeypatch.setenv("CREW_MODE", "true")
    _stub_role_responses(monkeypatch,
                         tester_text="t notes",
                         reviewer_text="reviewer wrote this")
    out = nodes_mod.review_node({
        "workflow_id": "wf-3",
        "task": "do something",
        "plan": [{"title": "step", "files": ["x.py"]}],
        "code_changes": [{"path": "x.py"}],
        "test_results": {"passed": True},
    })
    assert out["review_summary"] == "reviewer wrote this"


def test_review_node_falls_back_to_single_llm_when_crew_off(monkeypatch):
    monkeypatch.delenv("CREW_MODE", raising=False)
    called = {}
    def fake_chat(task_type, system, user, *, workflow_id=None, state=None):
        called["task_type"] = task_type
        return "single-llm summary"
    monkeypatch.setattr(nodes_mod, "_chat", fake_chat)
    out = nodes_mod.review_node({
        "workflow_id": "wf-4",
        "task": "do something",
        "plan": [],
        "code_changes": [],
        "test_results": {"passed": True},
        "crew_mode": False,
    })
    assert out["review_summary"] == "single-llm summary"
    assert called["task_type"] == "review"
