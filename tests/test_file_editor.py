"""File editor round-trip tests with a stub router. No network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.file_editor as fe_mod
from router.base import ChatResult


class StubRouter:
    def __init__(self, payloads):
        self._queue = list(payloads)
        self.calls = 0

    def chat(self, messages, task_type="simple", model=None, max_tokens=1024, temperature=0.7, workflow_id=None):
        self.calls += 1
        payload = self._queue.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return ChatResult(text=text, model="stub", provider="stub", prompt_tokens=1, completion_tokens=1)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    (tmp_path / "cli.py").write_text("import sys\nprint('hello')\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "lib.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr(fe_mod, "WORKING_TREE", tmp_path)
    return tmp_path


def _stub_router(monkeypatch, payloads):
    sr = StubRouter(payloads)
    monkeypatch.setattr(fe_mod, "_router", sr)
    return sr


def test_apply_plan_writes_new_content(workspace, monkeypatch):
    sr = _stub_router(monkeypatch, [{
        "changes": [
            {"path": "cli.py", "content": "import sys\n\ndef main():\n    print('hi')\n\nif __name__=='__main__':\n    main()\n"},
        ]
    }])
    plan = [{"title": "wrap in main", "detail": "", "files": ["cli.py"]}]
    changes = list(fe_mod.apply_plan(task="wrap", analysis="", plan=plan))
    assert len(changes) == 1
    assert changes[0]["path"] == "cli.py"
    assert "def main" in (workspace / "cli.py").read_text()
    assert changes[0]["before"].startswith("import sys")
    assert "+def main" in changes[0]["diff"]
    assert sr.calls == 1


def test_apply_plan_creates_missing_file(workspace, monkeypatch):
    _stub_router(monkeypatch, [{
        "changes": [{"path": "new/module.py", "content": "X = 1\n"}]
    }])
    plan = [{"title": "new file", "detail": "", "files": ["new/module.py"]}]
    list(fe_mod.apply_plan(task="add module", analysis="", plan=plan))
    assert (workspace / "new" / "module.py").read_text() == "X = 1\n"


def test_apply_plan_deletes_file_when_content_null(workspace, monkeypatch):
    _stub_router(monkeypatch, [{
        "changes": [{"path": "sub/lib.py", "content": None}]
    }])
    plan = [{"title": "drop", "detail": "", "files": ["sub/lib.py"]}]
    changes = list(fe_mod.apply_plan(task="drop", analysis="", plan=plan))
    assert not (workspace / "sub" / "lib.py").exists()
    assert changes[0]["after"] is None


def test_apply_plan_dry_run_does_not_touch_disk(workspace, monkeypatch):
    _stub_router(monkeypatch, [{
        "changes": [{"path": "cli.py", "content": "DESTROYED\n"}]
    }])
    plan = [{"title": "wreck", "detail": "", "files": ["cli.py"]}]
    list(fe_mod.apply_plan(task="x", analysis="", plan=plan, dry_run=True))
    assert (workspace / "cli.py").read_text() == "import sys\nprint('hello')\n"


def test_apply_plan_rejects_paths_outside_working_tree(workspace, monkeypatch):
    _stub_router(monkeypatch, [{
        "changes": [{"path": "../escape.py", "content": "pwn"}]
    }])
    plan = [{"title": "escape", "detail": "", "files": ["../escape.py"]}]
    changes = list(fe_mod.apply_plan(task="escape", analysis="", plan=plan))
    assert changes == []  # rejected
    assert not (workspace.parent / "escape.py").exists()


def test_apply_plan_repairs_one_bad_json_response(workspace, monkeypatch):
    sr = _stub_router(monkeypatch, [
        "not even close to JSON",  # first response: garbage
        {"changes": [{"path": "cli.py", "content": "ok\n"}]},  # repair attempt
    ])
    plan = [{"title": "ok", "detail": "", "files": ["cli.py"]}]
    changes = list(fe_mod.apply_plan(task="x", analysis="", plan=plan))
    assert sr.calls == 2
    assert (workspace / "cli.py").read_text() == "ok\n"


def test_apply_plan_handles_json_inside_fences(workspace, monkeypatch):
    _stub_router(monkeypatch, [
        '```json\n{"changes":[{"path":"cli.py","content":"fenced\\n"}]}\n```',
    ])
    plan = [{"title": "ok", "detail": "", "files": ["cli.py"]}]
    list(fe_mod.apply_plan(task="x", analysis="", plan=plan))
    assert (workspace / "cli.py").read_text() == "fenced\n"


def test_apply_plan_no_files_returns_empty(workspace, monkeypatch):
    sr = _stub_router(monkeypatch, [])  # no router call should happen
    plan = [{"title": "thinkpiece", "detail": "no files", "files": []}]
    changes = list(fe_mod.apply_plan(task="x", analysis="", plan=plan))
    assert changes == []
    assert sr.calls == 0


def test_apply_plan_rejects_too_many_files(workspace, monkeypatch):
    paths = [f"f{i}.py" for i in range(fe_mod.MAX_FILES_PER_CALL + 1)]
    plan = [{"title": "many", "detail": "", "files": paths}]
    with pytest.raises(ValueError, match="max"):
        list(fe_mod.apply_plan(task="x", analysis="", plan=plan))
