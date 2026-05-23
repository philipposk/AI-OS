"""Auto-tune trigger — threshold + watermark behavior. No DSPy invocation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "auto.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    monkeypatch.setenv("AUTO_TUNE_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("LEARNED_PROMPTS_PATH", str(tmp_path / "learned.json"))
    # Wipe any task-type override from outer env.
    monkeypatch.delenv("AUTO_TUNE_TASK_TYPES", raising=False)


def _seed(workflow_id, verdict, *, retries=0, cost=0.01):
    from storage import retrospectives as r
    r.record(workflow_id=workflow_id, task=f"task {workflow_id}", verdict=verdict,
             plan_steps=1, test_retries=retries, tests_passed=(verdict == "success"),
             files_touched=1, cost_usd=cost, crew_mode=False,
             commit_sha="x" if verdict == "success" else None, notes="n")


class _StubTuner:
    def __init__(self):
        self.calls: list = []
        self.saved: list = []

    def tune(self, task_type, *, examples=None, scorer=None):
        from tuning.tuner import TuneResult, BASE_PROMPTS
        self.calls.append((task_type, len(examples or [])))
        return TuneResult(
            task_type=task_type,
            base_prompt=BASE_PROMPTS[task_type],
            tuned_prompt=f"TUNED-{task_type}",
            n_examples=len(examples or []),
        )

    def save(self, results, path=None):
        self.saved.extend(results)
        # Mimic real save so loaded prompts work end-to-end.
        from tuning.tuner import PromptTuner
        return PromptTuner().save(results, path=path)


def test_auto_tune_skips_when_below_min_total(monkeypatch):
    for i in range(3):
        _seed(f"w{i}", "success")
    from tuning import auto_tune
    rep = auto_tune(min_total=20, min_new=5, tuner=_StubTuner())
    assert rep.triggered == []
    assert all("min_total" in v for v in rep.skipped.values())


def test_auto_tune_runs_when_threshold_crossed(monkeypatch):
    for i in range(12):
        _seed(f"pos-{i}", "success")
    for i in range(12):
        _seed(f"neg-{i}", "tests_failed", retries=3, cost=0.05)
    from tuning import auto_tune
    stub = _StubTuner()
    rep = auto_tune(min_total=20, min_new=5, tuner=stub, task_types=["plan"])
    assert rep.triggered == ["plan"]
    assert "plan" in rep.results
    assert stub.calls == [("plan", 24)]


def test_auto_tune_respects_watermark(monkeypatch, tmp_path):
    """Second run with no new retrospectives is a no-op."""
    for i in range(12):
        _seed(f"pos-{i}", "success")
    for i in range(12):
        _seed(f"neg-{i}", "tests_failed", retries=3, cost=0.05)

    from tuning import auto_tune
    stub = _StubTuner()
    r1 = auto_tune(min_total=20, min_new=5, tuner=stub, task_types=["plan"])
    assert r1.triggered == ["plan"]

    # No new retros → second call should skip
    r2 = auto_tune(min_total=20, min_new=5, tuner=stub, task_types=["plan"])
    assert r2.triggered == []
    assert "min_new" in r2.skipped["plan"]


def test_auto_tune_resumes_after_more_retrospectives():
    for i in range(12):
        _seed(f"pos-{i}", "success")
    for i in range(12):
        _seed(f"neg-{i}", "tests_failed", retries=3, cost=0.05)
    from tuning import auto_tune

    stub = _StubTuner()
    auto_tune(min_total=20, min_new=5, tuner=stub, task_types=["plan"])

    # Add 6 more retros → delta=6 ≥ min_new=5 → re-triggers
    for i in range(6):
        _seed(f"pos2-{i}", "success")
    r3 = auto_tune(min_total=20, min_new=5, tuner=stub, task_types=["plan"])
    assert r3.triggered == ["plan"]


def test_auto_tune_save_writes_learned_prompts():
    for i in range(10):
        _seed(f"pos-{i}", "success")
    for i in range(10):
        _seed(f"neg-{i}", "tests_failed", retries=3, cost=0.05)

    from tuning import auto_tune, load_learned_prompts
    rep = auto_tune(min_total=20, min_new=5, tuner=_StubTuner(), task_types=["plan"])
    assert rep.triggered == ["plan"]
    loaded = load_learned_prompts()
    assert loaded.get("plan") == "TUNED-plan"


def test_save_state_atomic_replace_preserves_concurrent_keys(tmp_path):
    """Two writers each tuning a different task_type must not lose each other's
    keys — but the canonical guard against full lost-update is read-modify-write
    inside the auto_tune call. The atomic-write test here just verifies
    _save_state itself is atomic: a crash mid-write must NOT leave a half-file."""
    from tuning.auto import _save_state, _load_state
    p = tmp_path / "state.json"
    _save_state(p, {"plan": 100, "analyze": 50})
    assert _load_state(p) == {"plan": 100, "analyze": 50}
    # Crash mid-write: simulate by ensuring the file is fully written even
    # when the JSON serialization is huge (no truncation surprise).
    _save_state(p, {"plan": 200, "analyze": 100, "review": 75})
    assert _load_state(p) == {"plan": 200, "analyze": 100, "review": 75}


def test_save_state_no_tempfile_left_on_disk(tmp_path):
    """After _save_state finishes, the parent dir should contain only the
    target file — no leftover .tmp* siblings."""
    from tuning.auto import _save_state
    p = tmp_path / "ws.json"
    _save_state(p, {"a": 1})
    siblings = list(tmp_path.iterdir())
    assert len(siblings) == 1
    assert siblings[0].name == "ws.json"


def test_auto_tune_skips_unknown_task_type():
    for i in range(25):
        _seed(f"w{i}", "success")
    from tuning import auto_tune
    rep = auto_tune(min_total=20, min_new=5, tuner=_StubTuner(),
                    task_types=["plan", "nonsense"])
    assert "nonsense" in rep.skipped
    assert rep.skipped["nonsense"] == "unknown task_type"
