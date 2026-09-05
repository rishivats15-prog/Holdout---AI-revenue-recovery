"""Pure statistics for the batch report. Every formula here is a function
of plain numbers — no database access, so each is directly unit-testable
against a hand-computed value.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ProportionCI:
    delta: float
    lower: float
    upper: float
    z: float


def two_proportion_ci(count_a: int, n_a: int, count_b: int, n_b: int, z: float = 1.96) -> ProportionCI:
    """Confidence interval on (rate_a - rate_b) via the standard normal
    approximation for the difference of two independent proportions.
    z=1.96 is the 95% default."""
    if n_a <= 0 or n_b <= 0:
        raise ValueError("both arms need at least one case to compute a confidence interval")
    p_a = count_a / n_a
    p_b = count_b / n_b
    delta = p_a - p_b
    se = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)
    margin = z * se
    return ProportionCI(delta=delta, lower=delta - margin, upper=delta + margin, z=z)


def rate(count: int, n: int) -> float:
    return count / n if n > 0 else 0.0


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
