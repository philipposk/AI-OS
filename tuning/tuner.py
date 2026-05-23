"""DSPy-driven prompt tuning.

Pipeline:
    retrospectives table  →  Examples  →  DSPy MIPROv2  →  learned_prompts.json

Each retrospective row is converted to a (task, verdict, notes) example.
DSPy treats `verdict == "success"` (with low retries, low cost) as good
trajectories and optimizes the system prompt to maximize that signal.

The optimizer is wrapped behind `PromptTuner.tune()` so the CLI is the
single entry point and tests can stub the heavy DSPy machinery.

Loaded prompts are read by `orchestrator.nodes` only when
`$USE_LEARNED_PROMPTS=1`, so a bad tune never silently degrades
production runs.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

LEARNED_PROMPTS_PATH = Path(__file__).parent / "learned_prompts.json"

# Maps task_type → the canonical "base" prompt that DSPy will learn to
# improve. Kept in lock-step with `orchestrator.nodes` constants. When you
# change a system prompt there, update the matching key here.
BASE_PROMPTS: Dict[str, str] = {
    "analyze": (
        "You are a senior engineer triaging a task before the planner sees it. "
        "Output <=100 words covering: (1) WHAT the user wants, (2) likely "
        "files/modules to touch, (3) the ONE risk that matters most."
    ),
    "plan": (
        "You are planning a small, reviewable code change. Output STRICT JSON only — "
        "an array of step objects."
    ),
    "review": (
        "Summarise the change in <=60 words for a human reviewer about to click "
        "approve. State exactly what the diff does, which files moved, and one "
        "residual risk worth a second look."
    ),
}


@dataclass
class TuningExample:
    task: str
    verdict: str
    retries: int
    cost_usd: float
    notes: str = ""

    @property
    def is_positive(self) -> bool:
        """A 'good trajectory' for the optimizer: succeeded on first try,
        no rejection, low cost. Tighten/loosen these thresholds based on
        your distribution of outcomes."""
        return (
            self.verdict == "success"
            and self.retries == 0
            and self.cost_usd <= 0.10
        )


def training_examples_from_retrospectives(
    *, min_per_class: int = 5,
) -> List[TuningExample]:
    """Pull rows from retrospectives, return a balanced list of examples.

    Returns [] if fewer than `min_per_class` positives or negatives exist —
    DSPy needs both sides to learn anything.
    """
    from storage import retrospectives as r

    pos: List[TuningExample] = []
    neg: List[TuningExample] = []
    for row in r.iter_entries():
        ex = TuningExample(
            task=row.task,
            verdict=row.verdict,
            retries=row.test_retries,
            cost_usd=row.cost_usd,
            notes=row.notes,
        )
        (pos if ex.is_positive else neg).append(ex)

    if len(pos) < min_per_class or len(neg) < min_per_class:
        logger.info("not enough training data: %d positive / %d negative (need %d each)",
                    len(pos), len(neg), min_per_class)
        return []
    return pos + neg


@dataclass
class TuneResult:
    task_type: str
    base_prompt: str
    tuned_prompt: str
    score_before: float = 0.0
    score_after: float = 0.0
    n_examples: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


class PromptTuner:
    """Wraps DSPy's optimizer behind a stable interface.

    Heavy import is lazy — importing `tuning` doesn't pull DSPy in until
    `.tune()` is called. That keeps test startup fast and lets CI run
    without GPU/network.
    """

    def __init__(self, *, lm: Optional[Any] = None):
        self.lm = lm

    def _ensure_dspy(self):
        try:
            import dspy
            return dspy
        except ImportError as e:
            raise RuntimeError(
                "dspy not installed; pip install dspy-ai"
            ) from e

    def configure_lm(self) -> Any:
        """Configure DSPy with our router as the LM, falling back to
        OpenAI through litellm if router env vars route there.

        Returns the configured dspy.LM instance.
        """
        dspy = self._ensure_dspy()
        if self.lm is not None:
            dspy.configure(lm=self.lm)
            return self.lm
        # Default: use litellm via dspy.LM. The user controls model via env.
        model = os.getenv("DSPY_TUNER_MODEL", "openai/gpt-4o-mini")
        lm = dspy.LM(model=model)
        dspy.configure(lm=lm)
        return lm

    def tune(
        self,
        task_type: str,
        *,
        examples: Optional[List[TuningExample]] = None,
        scorer: Optional[Callable[[Any, Any], float]] = None,
    ) -> TuneResult:
        """Run MIPROv2 on the base prompt for `task_type`. Returns a TuneResult.

        If `examples` is None, pulls from retrospectives. If insufficient
        data, returns a TuneResult with base==tuned and n_examples=0.
        """
        if task_type not in BASE_PROMPTS:
            raise ValueError(f"unknown task_type {task_type!r}; choose from {list(BASE_PROMPTS)}")
        base = BASE_PROMPTS[task_type]

        if examples is None:
            examples = training_examples_from_retrospectives()
        if not examples:
            return TuneResult(task_type=task_type, base_prompt=base, tuned_prompt=base, n_examples=0)

        dspy = self._ensure_dspy()
        self.configure_lm()

        # Define a tiny DSPy module — single-step task with the system
        # prompt as the optimizable instruction. MIPROv2 will mutate the
        # instruction string and pick the highest-scoring variant.
        class _TaskModule(dspy.Module):  # type: ignore[misc]
            def __init__(self):
                super().__init__()
                self.gen = dspy.ChainOfThought(f"task -> response")

            def forward(self, task):
                return self.gen(task=task)

        train = [dspy.Example(task=ex.task, label=ex.verdict).with_inputs("task") for ex in examples]

        def _scorer(example, pred, trace=None) -> float:
            if scorer is not None:
                return float(scorer(example, pred))
            # Default scorer: 1.0 if the prediction looks substantive and
            # the example was a success, else 0.0. Replace for real runs.
            resp = getattr(pred, "response", "") or ""
            label_ok = example.label == "success"
            return 1.0 if (label_ok and len(resp) >= 20) else 0.0

        try:
            optimizer = dspy.MIPROv2(metric=_scorer, num_threads=1, auto="light")
            compiled = optimizer.compile(_TaskModule(), trainset=train, requires_permission_to_run=False)
        except Exception as e:  # noqa: BLE001
            logger.warning("dspy optimization failed (%s); returning base prompt", e)
            return TuneResult(task_type=task_type, base_prompt=base, tuned_prompt=base,
                              n_examples=len(examples),
                              extra={"error": type(e).__name__ + ": " + str(e)[:200]})

        # Pull the optimized instruction back out. DSPy stores it on the
        # signature's `instructions`. Falls back to base prompt if shape changes.
        tuned = base
        try:
            tuned = compiled.gen.signature.instructions or base
        except Exception:  # noqa: BLE001
            pass

        return TuneResult(
            task_type=task_type,
            base_prompt=base,
            tuned_prompt=tuned,
            n_examples=len(examples),
        )

    def save(self, results: List[TuneResult], path: Path = LEARNED_PROMPTS_PATH) -> Path:
        """Write {task_type: tuned_prompt, ...} to JSON. Existing entries
        are merged so partial re-runs don't clobber prior tunes.
        """
        payload: Dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text())
            except Exception:  # noqa: BLE001
                payload = {}
        for r in results:
            payload[r.task_type] = {
                "tuned_prompt": r.tuned_prompt,
                "base_prompt": r.base_prompt,
                "n_examples": r.n_examples,
            }
        path.write_text(json.dumps(payload, indent=2))
        return path


def load_learned_prompts(path: Optional[Path] = None) -> Dict[str, str]:
    """Return {task_type: tuned_prompt} or {} if file missing/invalid.

    Called by `orchestrator.nodes` ONLY when `$USE_LEARNED_PROMPTS=1`.
    A bad file never throws — degrades gracefully to base prompts.

    `path` defaults to `$LEARNED_PROMPTS_PATH` env var if set, else the
    module constant. Env override keeps tests hermetic without monkeypatching.
    """
    if path is None:
        env_path = os.getenv("LEARNED_PROMPTS_PATH", "").strip()
        path = Path(env_path) if env_path else LEARNED_PROMPTS_PATH
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[str, str] = {}
    for task_type, entry in raw.items():
        if isinstance(entry, dict) and isinstance(entry.get("tuned_prompt"), str):
            out[task_type] = entry["tuned_prompt"]
        elif isinstance(entry, str):
            out[task_type] = entry
    return out
