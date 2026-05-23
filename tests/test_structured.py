"""Structured outputs via instructor + pydantic + litellm. No network."""
from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "struct.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.delenv("USE_STRUCTURED_OUTPUTS", raising=False)


def _install_fake_instructor(monkeypatch, *, plan_steps=None, summary="ok"):
    """Inject fakes for instructor + litellm + pydantic that return canned data."""
    plan_steps = plan_steps or [{"title": "Add x", "detail": "do it", "files": ["a.py"]}]

    # Fake pydantic — use real if available; otherwise stub.
    import pydantic  # let real pydantic do the validation work

    # Fake instructor: client.messages.create(response_model=X, ...) → X(...)
    class _Messages:
        def __init__(self, planned, review):
            self._planned = planned
            self._review = review

        def create(self, *, model, response_model, messages, max_tokens=1024, **kw):
            # Decide which payload by class name (test-only switch).
            if response_model.__name__ == "PlannedTask":
                return response_model(steps=self._planned)
            if response_model.__name__ == "ReviewSummary":
                return response_model(summary=self._review)
            raise RuntimeError(f"unexpected response_model {response_model}")

    class _Client:
        def __init__(self, planned, review):
            self.messages = _Messages(planned, review)

    inst_mod = types.SimpleNamespace()
    inst_mod.from_litellm = lambda fn: _Client(plan_steps, summary)
    monkeypatch.setitem(sys.modules, "instructor", inst_mod)

    lite_mod = types.SimpleNamespace()
    lite_mod.completion = lambda **kw: {}
    monkeypatch.setitem(sys.modules, "litellm", lite_mod)


def test_disabled_by_default(monkeypatch):
    from orchestrator import structured
    assert structured.structured_plan(task="t", analysis="a") is None
    assert structured.structured_review(task="t", plan_titles=[], files_touched=[], tests_passed=True) is None


def test_disabled_when_deps_missing(monkeypatch):
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")
    # Force ImportError: nuke instructor from sys.modules and shadow its import
    import importlib
    monkeypatch.setitem(sys.modules, "instructor", None)
    from orchestrator import structured
    # `_try_imports` re-imports — with None in sys.modules, `import instructor` raises
    assert structured.structured_plan(task="t", analysis="a") is None


def test_structured_plan_returns_steps_when_enabled(monkeypatch):
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")
    _install_fake_instructor(monkeypatch, plan_steps=[
        {"title": "Step one", "detail": "do thing", "files": ["x.py"]},
        {"title": "Step two", "detail": "do other", "files": []},
    ])
    from orchestrator import structured
    out = structured.structured_plan(task="add x", analysis="small change")
    assert out is not None
    assert len(out) == 2
    assert out[0]["title"] == "Step one"
    assert out[1]["files"] == []


def test_structured_plan_caps_at_5(monkeypatch):
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")
    # Pydantic max_length=5 will REJECT >5; supply exactly 5.
    steps = [{"title": f"S{i}", "detail": "d", "files": []} for i in range(5)]
    _install_fake_instructor(monkeypatch, plan_steps=steps)
    from orchestrator import structured
    out = structured.structured_plan(task="t", analysis="a")
    assert len(out) == 5


def test_structured_review_returns_summary(monkeypatch):
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")
    _install_fake_instructor(monkeypatch, summary="Added x to y. No residual risk.")
    from orchestrator import structured
    out = structured.structured_review(
        task="t", plan_titles=["a", "b"], files_touched=["x.py"], tests_passed=True,
    )
    assert out == "Added x to y. No residual risk."


def test_structured_plan_returns_none_when_llm_errors(monkeypatch):
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                raise RuntimeError("provider blew up")

    inst_mod = types.SimpleNamespace(from_litellm=lambda fn: _Client())
    monkeypatch.setitem(sys.modules, "instructor", inst_mod)
    monkeypatch.setitem(sys.modules, "litellm", types.SimpleNamespace(completion=lambda **kw: {}))

    from orchestrator import structured
    assert structured.structured_plan(task="t", analysis="a") is None


def test_structured_plan_returns_none_when_result_malformed(monkeypatch):
    """If client returns a non-PlannedTask shape (e.g. missing .steps),
    the post-call extraction must fall back too, not crash."""
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")

    class _Bad:
        # No .steps attribute on purpose.
        pass

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                return _Bad()

    monkeypatch.setitem(sys.modules, "instructor",
                        types.SimpleNamespace(from_litellm=lambda fn: _Client()))
    monkeypatch.setitem(sys.modules, "litellm",
                        types.SimpleNamespace(completion=lambda **kw: {}))

    from orchestrator import structured
    assert structured.structured_plan(task="t", analysis="a") is None


def test_structured_review_returns_none_when_result_malformed(monkeypatch):
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")

    class _Bad:
        pass

    class _Client:
        class messages:
            @staticmethod
            def create(**kw):
                return _Bad()

    monkeypatch.setitem(sys.modules, "instructor",
                        types.SimpleNamespace(from_litellm=lambda fn: _Client()))
    monkeypatch.setitem(sys.modules, "litellm",
                        types.SimpleNamespace(completion=lambda **kw: {}))

    from orchestrator import structured
    out = structured.structured_review(task="t", plan_titles=[], files_touched=[], tests_passed=True)
    assert out is None


def test_plan_node_uses_structured_when_enabled(monkeypatch):
    """plan_node calls structured_plan first; on success the legacy _chat path is skipped."""
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")
    monkeypatch.delenv("CREW_MODE", raising=False)
    _install_fake_instructor(monkeypatch, plan_steps=[
        {"title": "Structured step", "detail": "from instructor", "files": ["cli.py"]},
    ])

    chat_calls = {"n": 0}
    def fake_chat(*a, **kw):
        chat_calls["n"] += 1
        return "[]"

    from orchestrator import nodes
    monkeypatch.setattr(nodes, "_chat", fake_chat)

    out = nodes.plan_node({"task": "do x", "analysis": "tiny", "workflow_id": "wf-s"})
    assert chat_calls["n"] == 0
    assert out["plan"][0]["title"] == "Structured step"


def test_plan_node_falls_back_when_structured_returns_none(monkeypatch):
    """When structured_plan returns None, plan_node uses the legacy _chat + json path."""
    monkeypatch.setenv("USE_STRUCTURED_OUTPUTS", "1")
    monkeypatch.delenv("CREW_MODE", raising=False)

    # Make structured_plan return None
    from orchestrator import structured as struct_mod
    monkeypatch.setattr(struct_mod, "structured_plan", lambda **kw: None)

    chat_calls = {"n": 0}
    def fake_chat(*a, **kw):
        chat_calls["n"] += 1
        return '[{"title": "fallback step", "detail": "d", "files": []}]'

    from orchestrator import nodes
    monkeypatch.setattr(nodes, "_chat", fake_chat)

    out = nodes.plan_node({"task": "do x", "analysis": "tiny", "workflow_id": "wf-fb"})
    assert chat_calls["n"] == 1
    assert out["plan"][0]["title"] == "fallback step"
