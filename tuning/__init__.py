"""Phase Z2: prompt tuning via DSPy.

Reads `storage.retrospectives` rows, formats them as training examples,
and runs DSPy's MIPROv2 optimizer to produce improved versions of the
analyze/plan/review system prompts.

Opt-in: nothing here runs unless `cli.py tune` is invoked. The tuned
prompts are written to `tuning/learned_prompts.json` and loaded by
`nodes.py` IF `$USE_LEARNED_PROMPTS=1`.
"""
from .auto import AUTO_STATE_PATH, AutoTuneReport, auto_tune
from .tuner import (
    LEARNED_PROMPTS_PATH,
    PromptTuner,
    TuneResult,
    load_learned_prompts,
    training_examples_from_retrospectives,
)

__all__ = [
    "AUTO_STATE_PATH",
    "AutoTuneReport",
    "LEARNED_PROMPTS_PATH",
    "PromptTuner",
    "TuneResult",
    "auto_tune",
    "load_learned_prompts",
    "training_examples_from_retrospectives",
]
