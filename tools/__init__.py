"""Analysis tools shared by every condition (SPEC.md sections 4 and 5.4)."""

from .analysis import (
    compile_law, disagreement, disagreement_many, fit_law, plan_shot, predict, predict_many,
    simulate, simulate_specs,
)
from .integrator import CompiledLaw, integrate

__all__ = [
    "CompiledLaw", "compile_law", "disagreement", "disagreement_many", "fit_law", "integrate",
    "plan_shot", "predict", "predict_many", "simulate", "simulate_specs",
]
