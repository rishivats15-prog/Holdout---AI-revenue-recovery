"""Predicts what the batch's per-arm recovery rates *should* be, straight
from sim/ground_truth.yaml, so the measured numbers can be checked against
a known answer.

This is the whole reason the submission runs on synthetic data: against a
live gateway there is no true effect to check a measurement against, so a
confidence interval is something you either trust or you don't. Here the
true effect is written down in a YAML file, and the report can show the
measured lift landing on it.

Two corrections separate the raw `treatment_uplift` numbers in the YAML
from what the report can possibly measure, and both are arithmetic, not
judgement:

1. **The attribution window.** sim schedules a recovering case's payment
   uniformly across days 1..recovery_scheduling_max_day, deliberately
   wider than the 14-day window eval counts. So only
   `window_days / max_day` of recoveries are ever countable, in *both*
   arms — which scales the observable lift down by the same factor.

2. **Intent-to-treat.** sim/outcomes.py applies the uplift by ARM, not by
   whether a touch actually landed: a treatment-arm case that got
   suppressed at rung 0 for DND still carries its cause's uplift. That is
   the correct thing to predict, because a holdout comparison measures
   intent-to-treat by construction — the denominator is every case
   assigned to treatment, including the ones policy stopped. Predicting a
   treated-only effect here would be marking our own homework against a
   population the experiment never isolated.

Both corrections are applied per case against that case's TRUE root
cause — this module and sim/outcomes.py are the only readers of
Case.true_root_cause in the system.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.enums import ExperimentArm
from eval.attribution import ATTRIBUTION_WINDOW_DAYS, assert_sim_agrees
from sim.config import load_ground_truth

MAX_RECOVERY_PROBABILITY = 0.95  # mirrors the ceiling determine_recovery() clamps to


def window_factor() -> float:
    """Fraction of a recovering case's possible payment days that fall
    inside the attribution window. sim draws recovery_day uniformly over
    the integers 1..recovery_scheduling_max_day, so this is just the share
    of that range at or below the window."""
    gt = load_ground_truth()
    assert_sim_agrees(gt.attribution_window_days)
    countable_days = min(ATTRIBUTION_WINDOW_DAYS, gt.recovery_scheduling_max_day)
    return countable_days / gt.recovery_scheduling_max_day


def true_recovery_probability(true_cause: str, arm: str) -> float:
    """The un-windowed probability sim/outcomes.py would draw against for a
    case with this true cause in this arm."""
    gt = load_ground_truth()
    base = gt.natural_recovery_rate[true_cause]
    if arm != ExperimentArm.TREATMENT.value:
        return base
    return min(base + gt.treatment_uplift[true_cause], MAX_RECOVERY_PROBABILITY)


def expected_in_window_rate(true_causes: list[str], arm: str) -> float | None:
    """Expected share of these cases that record a countable recovery —
    the mean of each case's own probability, so the batch's actual
    root-cause mix is respected rather than the YAML's nominal
    distribution. Returns None for an empty arm."""
    if not true_causes:
        return None
    factor = window_factor()
    return sum(true_recovery_probability(cause, arm) * factor for cause in true_causes) / len(true_causes)


@dataclass(frozen=True)
class GroundTruthCheck:
    window_factor: float
    expected_treatment_rate: float | None
    expected_holdout_rate: float | None
    expected_incremental_rate: float | None
    measured_incremental_rate: float
    absolute_error: float | None
    expected_within_ci: bool | None
    nominal_mean_uplift: float | None  # the un-windowed YAML effect, for context


def check_against_ground_truth(
    treatment_true_causes: list[str],
    holdout_true_causes: list[str],
    measured_incremental_rate: float,
    ci_lower: float | None,
    ci_upper: float | None,
) -> GroundTruthCheck:
    expected_treatment = expected_in_window_rate(treatment_true_causes, ExperimentArm.TREATMENT.value)
    expected_holdout = expected_in_window_rate(holdout_true_causes, ExperimentArm.HOLDOUT.value)

    expected_incremental = None
    if expected_treatment is not None and expected_holdout is not None:
        expected_incremental = expected_treatment - expected_holdout

    absolute_error = None
    within_ci = None
    if expected_incremental is not None:
        absolute_error = abs(measured_incremental_rate - expected_incremental)
        if ci_lower is not None and ci_upper is not None:
            within_ci = ci_lower <= expected_incremental <= ci_upper

    return GroundTruthCheck(
        window_factor=window_factor(),
        expected_treatment_rate=expected_treatment,
        expected_holdout_rate=expected_holdout,
        expected_incremental_rate=expected_incremental,
        measured_incremental_rate=measured_incremental_rate,
        absolute_error=absolute_error,
        expected_within_ci=within_ci,
        nominal_mean_uplift=_nominal_mean_uplift(treatment_true_causes),
    )


def _nominal_mean_uplift(true_causes: list[str]) -> float | None:
    """The YAML's treatment effect for this case mix with no window
    correction — what the lift would be if every recovery were countable.
    Reported alongside the windowed prediction so the gap between the two
    is visible rather than looking like measurement error."""
    if not true_causes:
        return None
    gt = load_ground_truth()
    lifts = [
        min(gt.natural_recovery_rate[c] + gt.treatment_uplift[c], MAX_RECOVERY_PROBABILITY) - gt.natural_recovery_rate[c]
        for c in true_causes
    ]
    return sum(lifts) / len(lifts)
