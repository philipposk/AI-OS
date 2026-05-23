"""Auto-tune trigger — runs PromptTuner when retrospective count crosses a threshold.

Designed to be called from cron / systemd timer / GitHub Actions schedule.
The single entry point `auto_tune()` is idempotent and cheap when there's
nothing to do — safe to call every hour.

Behavior:
  1. Read aggregate retrospective stats.
  2. For each task_type in (analyze, plan, review), check whether enough
     NEW retrospectives have accumulated since the last tune. Per-task-type
     watermark is stored in `tuning/auto_state.json`.
  3. If new ≥ `min_new` and total ≥ `min_total`, run `PromptTuner.tune()`,
     save the result, update the watermark.
  4. Return a report dict suitable for logging or printing.

The watermark file is the only mutable state. Delete it to force a re-tune.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .tuner import (
    BASE_PROMPTS,
    LEARNED_PROMPTS_PATH,
    PromptTuner,
    TuneResult,
    training_examples_from_retrospectives,
)

logger = logging.getLogger(__name__)

AUTO_STATE_PATH = Path(__file__).parent / "auto_state.json"


@dataclass
class AutoTuneReport:
    triggered: List[str] = field(default_factory=list)   # task_types that ran
    skipped: Dict[str, str] = field(default_factory=dict)  # task_type → reason
    results: Dict[str, TuneResult] = field(default_factory=dict)
    total_retrospectives: int = 0

    def to_dict(self) -> dict:
        return {
            "total_retrospectives": self.total_retrospectives,
            "triggered": self.triggered,
            "skipped": self.skipped,
            "results": {
                t: {
                    "n_examples": r.n_examples,
                    "changed": r.tuned_prompt != r.base_prompt,
                }
                for t, r in self.results.items()
            },
        }


def _load_state(path: Path) -> Dict[str, int]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_state(path: Path, state: Dict[str, int]) -> None:
    """Atomic write: temp file in the same directory + os.replace.

    Concurrent cron invocations could otherwise overwrite each other's
    watermarks — process B reads {plan:50}, A finishes and writes
    {plan:100}, B writes {analyze:100} → A's update is lost. The temp
    file is also opened with the directory mode so the replace is a
    same-filesystem rename (atomic on POSIX + Windows).
    """
    import os as _os
    import tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with _os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        _os.replace(tmp, path)
    except Exception:
        try:
            _os.unlink(tmp)
        except OSError:
            pass
        raise


def _current_retrospective_count() -> int:
    from storage import retrospectives as r
    return r.aggregate().get("total", 0)


def auto_tune(
    *,
    min_total: int = 20,
    min_new: int = 10,
    task_types: Optional[List[str]] = None,
    state_path: Optional[Path] = None,
    learned_path: Optional[Path] = None,
    tuner: Optional[PromptTuner] = None,
) -> AutoTuneReport:
    """Run tuning for any task_type whose new-retrospective delta crossed `min_new`.

    Env overrides (all optional):
        AUTO_TUNE_MIN_TOTAL — int, default 20
        AUTO_TUNE_MIN_NEW   — int, default 10
        AUTO_TUNE_TASK_TYPES — comma list, default "plan,analyze,review"
        AUTO_TUNE_STATE_PATH — path to JSON watermark file
        LEARNED_PROMPTS_PATH — where to save tuned prompts (also read by nodes)
    """
    min_total = int(os.getenv("AUTO_TUNE_MIN_TOTAL", str(min_total)))
    min_new = int(os.getenv("AUTO_TUNE_MIN_NEW", str(min_new)))
    if task_types is None:
        raw = os.getenv("AUTO_TUNE_TASK_TYPES", "plan,analyze,review").strip()
        task_types = [t.strip() for t in raw.split(",") if t.strip()]
    if state_path is None:
        env_p = os.getenv("AUTO_TUNE_STATE_PATH", "").strip()
        state_path = Path(env_p) if env_p else AUTO_STATE_PATH
    if learned_path is None:
        env_p = os.getenv("LEARNED_PROMPTS_PATH", "").strip()
        learned_path = Path(env_p) if env_p else LEARNED_PROMPTS_PATH

    report = AutoTuneReport()
    report.total_retrospectives = _current_retrospective_count()

    if report.total_retrospectives < min_total:
        for t in task_types:
            report.skipped[t] = f"total {report.total_retrospectives} < min_total {min_total}"
        return report

    state = _load_state(state_path)
    tuner = tuner or PromptTuner()
    new_state = dict(state)

    for tt in task_types:
        if tt not in BASE_PROMPTS:
            report.skipped[tt] = f"unknown task_type"
            continue
        last_seen = int(state.get(tt, 0))
        delta = report.total_retrospectives - last_seen
        if delta < min_new:
            report.skipped[tt] = f"delta {delta} < min_new {min_new}"
            continue

        examples = training_examples_from_retrospectives(min_per_class=max(3, min_new // 2))
        if not examples:
            report.skipped[tt] = "insufficient balanced examples"
            continue

        try:
            result = tuner.tune(tt, examples=examples)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_tune tune(%s) failed: %s", tt, e)
            report.skipped[tt] = f"tune error: {type(e).__name__}"
            continue

        try:
            tuner.save([result], path=learned_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_tune save failed: %s", e)
            report.skipped[tt] = f"save error: {type(e).__name__}"
            continue

        report.triggered.append(tt)
        report.results[tt] = result
        new_state[tt] = report.total_retrospectives

    if new_state != state:
        try:
            _save_state(state_path, new_state)
        except Exception as e:  # noqa: BLE001
            logger.warning("auto_tune watermark save failed: %s", e)

    return report
