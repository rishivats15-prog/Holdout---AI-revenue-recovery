"""Loads and validates sim/ground_truth.yaml — the one place the synthetic
world's true root-cause distribution and natural-recovery baseline live.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from db.enums import RootCause

GROUND_TRUTH_PATH = Path(__file__).resolve().parent / "ground_truth.yaml"


@dataclass(frozen=True)
class GroundTruth:
    lane: str
    seed: int
    root_cause_distribution: dict[str, float]
    natural_recovery_rate: dict[str, float]
    treatment_uplift: dict[str, float]
    attribution_window_days: int
    recovery_scheduling_max_day: int
    promise_prompt_rate: float
    promise_days_min: int
    promise_days_max: int
    dispute_rate: float
    ambiguous_signal_rate: float
    retries_min: int
    retries_max: int
    exposure_median_paise: int
    exposure_sigma: float
    exposure_min_paise: int
    exposure_max_paise: int


@lru_cache(maxsize=None)
def load_ground_truth(path: Path = GROUND_TRUTH_PATH) -> GroundTruth:
    raw = yaml.safe_load(path.read_text())

    causes = set(raw["root_cause_distribution"])
    valid_causes = {c.value for c in RootCause}
    unknown = causes - valid_causes
    if unknown:
        raise ValueError(f"ground truth references unknown root causes: {unknown}")

    total_weight = sum(raw["root_cause_distribution"].values())
    if not (0.99 <= total_weight <= 1.01):
        raise ValueError(f"root_cause_distribution weights must sum to ~1.0, got {total_weight}")

    missing_baseline = causes - set(raw["natural_recovery_rate"])
    if missing_baseline:
        raise ValueError(f"natural_recovery_rate missing entries for: {missing_baseline}")

    missing_uplift = causes - set(raw["treatment_uplift"])
    if missing_uplift:
        raise ValueError(f"treatment_uplift missing entries for: {missing_uplift}")

    for cause in causes:
        combined = raw["natural_recovery_rate"][cause] + raw["treatment_uplift"][cause]
        if not (0.0 <= combined <= 1.0):
            raise ValueError(f"natural_recovery_rate + treatment_uplift for {cause!r} must stay within [0, 1], got {combined}")

    ptp = raw["promise_to_pay"]
    recovery_max_day = raw["recovery_scheduling_max_day"]
    if recovery_max_day < raw["attribution_window_days"]:
        raise ValueError("recovery_scheduling_max_day must be >= attribution_window_days, or eval never sees a case excluded by the window")

    return GroundTruth(
        lane=raw["lane"],
        seed=raw["seed"],
        root_cause_distribution=raw["root_cause_distribution"],
        natural_recovery_rate=raw["natural_recovery_rate"],
        treatment_uplift=raw["treatment_uplift"],
        attribution_window_days=raw["attribution_window_days"],
        recovery_scheduling_max_day=recovery_max_day,
        promise_prompt_rate=ptp["prompt_rate"],
        promise_days_min=ptp["promise_days_min"],
        promise_days_max=ptp["promise_days_max"],
        dispute_rate=raw["dispute_rate"],
        ambiguous_signal_rate=raw["ambiguous_signal_rate"],
        retries_min=raw["retries_per_episode"]["min"],
        retries_max=raw["retries_per_episode"]["max"],
        exposure_median_paise=raw["exposure_amount"]["median_paise"],
        exposure_sigma=raw["exposure_amount"]["sigma"],
        exposure_min_paise=raw["exposure_amount"]["min_paise"],
        exposure_max_paise=raw["exposure_amount"]["max_paise"],
    )
