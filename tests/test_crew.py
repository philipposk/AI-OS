"""Phase W: crew bus + coordinator + plan_node integration."""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def sandbox_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "test.sqlite"))
    monkeypatch.delenv("CREW_MODE", raising=False)
    # Reset the bus singleton so each test gets a fresh subscriber list.
    import orchestrator.crew.bus as bus_mod
    bus_mod._bus_singleton = None
    yield


# ---------- bus ----------


def test_bus_post_persists_and_returns_message():
    from orchestrator.crew.bus import get_bus, MessageBus

    bus = get_bus()
    m = bus.post(workflow_id="wf-1", role="planner", content="hello",
                 meta={"model": "x"})
    assert m.id > 0
    assert m.role == "planner"
    transcript = MessageBus.transcript("wf-1")
    assert len(transcript) == 1
    assert transcript[0].content == "hello"
    assert transcript[0].meta["model"] == "x"


def test_bus_tail_cursor():
    from orchestrator.crew.bus import get_bus, MessageBus

    bus = get_bus()
    a = bus.post(workflow_id="wf-1", role="planner", content="a")
    b = bus.post(workflow_id="wf-1", role="critic", content="b")
    c = bus.post(workflow_id="wf-1", role="planner", content="c")
    after_a = MessageBus.tail("wf-1", after_id=a.id)
    assert [m.content for m in after_a] == ["b", "c"]
    after_c = MessageBus.tail("wf-1", after_id=c.id)
    assert after_c == []


def test_bus_subscriber_fires():
    from orchestrator.crew.bus import get_bus

    bus = get_bus()
    seen: list = []
    bus.subscribe(seen.append)
    bus.post(workflow_id="wf-x", role="coordinator", content="ping")
    assert len(seen) == 1 and seen[0].role == "coordinator"


# ---------- is_crew_mode ----------


def test_is_crew_mode_env(monkeypatch):
    from orchestrator.crew import is_crew_mode

    monkeypatch.setenv("CREW_MODE", "true")
    assert is_crew_mode({}) is True
    monkeypatch.setenv("CREW_MODE", "false")
    assert is_crew_mode({}) is False


def test_is_crew_mode_state_beats_env(monkeypatch):
    from orchestrator.crew import is_crew_mode

    monkeypatch.setenv("CREW_MODE", "true")
    assert is_crew_mode({"crew_mode": False}) is False
    monkeypatch.delenv("CREW_MODE", raising=False)
    assert is_crew_mode({"crew_mode": True}) is True


# ---------- coordinator ----------


def test_coordinate_plan_writes_transcript(monkeypatch):
    """Stub the two roles, verify the coordinator posts the expected
    transcript and returns parsed JSON.
    """
    from orchestrator.crew import coordinator
    from orchestrator.crew.bus import MessageBus
    from orchestrator.crew.roles import RoleResult

    draft_steps = [{"title": "T1", "detail": "d", "files": []}]
    revised_steps = [{"title": "T1-revised", "detail": "d2", "files": ["a"]}]

    def fake_respond(self, user, **kw):
        if self.name == "planner":
            text = json.dumps(revised_steps if "Revise" in user else draft_steps)
        elif self.name == "critic":
            text = "missing tests"
        else:
            text = ""
        return RoleResult(role=self.name, content=text, model="x", provider="y",
                          prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr("orchestrator.crew.roles.Role.respond", fake_respond)

    out = coordinator.coordinate_plan("Add foo", "small change", workflow_id="wf-9")
    assert out == revised_steps

    transcript = MessageBus.transcript("wf-9")
    roles = [m.role for m in transcript]
    # coordinator start, planner draft, critic, planner revision, coordinator final
    assert roles == ["coordinator", "planner", "critic", "planner", "coordinator"]
    revisions = [m for m in transcript if m.role == "planner" and m.meta.get("revision")]
    assert len(revisions) == 1


def test_coordinate_plan_falls_back_on_unparseable_revision(monkeypatch):
    from orchestrator.crew import coordinator
    from orchestrator.crew.roles import RoleResult

    draft = [{"title": "T", "detail": "d", "files": []}]

    def fake_respond(self, user, **kw):
        if self.name == "planner":
            return RoleResult(role="planner",
                              content=("not json" if "Revise" in user else json.dumps(draft)),
                              model="x", provider="y", prompt_tokens=1, completion_tokens=1)
        return RoleResult(role="critic", content="meh",
                          model="x", provider="y", prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr("orchestrator.crew.roles.Role.respond", fake_respond)

    out = coordinator.coordinate_plan("t", "a", workflow_id="wf-x")
    assert out == draft  # fell back to draft because revision wasn't parseable


# ---------- plan_node integration ----------


def test_plan_node_uses_crew_when_enabled(monkeypatch):
    from orchestrator import nodes

    called = {"crew": 0, "chat": 0}

    monkeypatch.setattr(nodes, "_chat",
                        lambda *a, **kw: (called.__setitem__("chat", called["chat"] + 1) or "[]"))
    monkeypatch.setattr("orchestrator.crew.coordinate_plan",
                        lambda task, analysis, workflow_id=None:
                        (called.__setitem__("crew", called["crew"] + 1)
                         or [{"title": "T", "detail": "d", "files": []}]))
    monkeypatch.setattr("orchestrator.crew.is_crew_mode", lambda state: True)

    out = nodes.plan_node({"task": "Add x", "analysis": "small",
                           "workflow_id": "wf-c", "crew_mode": True})
    assert called["crew"] == 1
    assert called["chat"] == 0
    assert out["plan"] and out["plan"][0]["title"] == "T"


def test_plan_node_skips_crew_when_disabled(monkeypatch):
    from orchestrator import nodes

    called = {"crew": 0, "chat": 0}

    def fake_chat(*a, **kw):
        called["chat"] += 1
        return "[{\"title\": \"single\", \"detail\": \"d\", \"files\": []}]"

    monkeypatch.setattr(nodes, "_chat", fake_chat)
    monkeypatch.setattr("orchestrator.crew.coordinate_plan",
                        lambda *a, **kw: (called.__setitem__("crew", called["crew"] + 1) or []))
    monkeypatch.setattr("orchestrator.crew.is_crew_mode", lambda state: False)

    out = nodes.plan_node({"task": "Add x", "analysis": "small", "workflow_id": "wf-c"})
    assert called["chat"] == 1
    assert called["crew"] == 0
    assert out["plan"][0]["title"] == "single"
