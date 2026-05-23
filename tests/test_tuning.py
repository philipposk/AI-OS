"""DSPy prompt tuning scaffold — tests without invoking the real optimizer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_COMPANY_DB", str(tmp_path / "tune.sqlite"))
    monkeypatch.setenv("EMBEDDINGS_BACKEND", "none")
    yield


def _seed(workflow_id, verdict, retries=0, cost=0.01):
    from storage import retrospectives as r
    r.record(workflow_id=workflow_id, task=f"task {workflow_id}", verdict=verdict,
             plan_steps=1, test_retries=retries, tests_passed=(verdict == "success"),
             files_touched=1, cost_usd=cost, crew_mode=False,
             commit_sha="x" if verdict == "success" else None,
             notes="n")


def test_training_examples_empty_when_no_data():
    from tuning import training_examples_from_retrospectives
    assert training_examples_from_retrospectives() == []


def test_training_examples_balanced_pos_neg():
    for i in range(6):
        _seed(f"pos-{i}", "success", retries=0, cost=0.01)
    for i in range(6):
        _seed(f"neg-{i}", "tests_failed", retries=3, cost=0.05)

    from tuning import training_examples_from_retrospectives
    ex = training_examples_from_retrospectives(min_per_class=5)
    assert len(ex) == 12
    pos = [e for e in ex if e.is_positive]
    neg = [e for e in ex if not e.is_positive]
    assert len(pos) == 6
    assert len(neg) == 6


def test_tune_returns_base_when_insufficient_data():
    from tuning import PromptTuner
    out = PromptTuner().tune("plan")
    assert out.n_examples == 0
    assert out.tuned_prompt == out.base_prompt


def test_save_and_load_learned_prompts(tmp_path):
    from tuning import PromptTuner, load_learned_prompts
    from tuning.tuner import TuneResult

    path = tmp_path / "learned.json"
    PromptTuner().save([TuneResult(task_type="plan", base_prompt="OLD",
                                    tuned_prompt="NEW", n_examples=10)], path=path)
    assert path.exists()
    loaded = load_learned_prompts(path)
    assert loaded == {"plan": "NEW"}


def test_load_learned_prompts_returns_empty_on_missing(tmp_path):
    from tuning import load_learned_prompts
    assert load_learned_prompts(tmp_path / "missing.json") == {}


def test_maybe_learned_prompt_falls_back_when_flag_off(monkeypatch):
    monkeypatch.delenv("USE_LEARNED_PROMPTS", raising=False)
    from orchestrator.nodes import _maybe_learned_prompt
    assert _maybe_learned_prompt("plan", "BASE") == "BASE"


def test_maybe_learned_prompt_uses_learned_when_flag_on(monkeypatch, tmp_path):
    monkeypatch.setenv("USE_LEARNED_PROMPTS", "1")
    custom = tmp_path / "lp.json"
    custom.write_text(json.dumps({"plan": {"tuned_prompt": "LEARNED", "base_prompt": "B"}}))
    monkeypatch.setenv("LEARNED_PROMPTS_PATH", str(custom))
    from orchestrator.nodes import _maybe_learned_prompt
    assert _maybe_learned_prompt("plan", "BASE") == "LEARNED"
