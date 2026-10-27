"""本地自出题、自解题的 Coding Memory 端到端评测."""

from benchmarks.coding.selftest.cases import NOISE_COUNTS, NOISE_LEVELS, Case, build_cases
from benchmarks.coding.selftest.runner import BASELINE, TUNED, run_selftest
from benchmarks.coding.selftest.solver import SolveResult, solve

__all__ = [
    "BASELINE",
    "NOISE_COUNTS",
    "NOISE_LEVELS",
    "TUNED",
    "Case",
    "SolveResult",
    "build_cases",
    "run_selftest",
    "solve",
]
