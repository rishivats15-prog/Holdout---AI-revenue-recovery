"""eval/groundtruth.py — the prediction the batch report is checked
against, and the proof that the estimator it's checking is unbiased.

The convergence test at the bottom is the important one. A single batch's
measured lift landing inside its confidence interval could be luck; that
test shows the windowed in-window recovery rate converges on the
predicted rate as the population grows, so the prediction is the right
thing to check a batch against in the first place.
"""

from __future__ import annotations

import pytest

from eval.attribution import ATTRIBUTION_WINDOW_DAYS, assert_sim_agrees
from eval.groundtruth import (
    MAX_RECOVERY_PROBABILITY,
    check_against_ground_truth,
    expected_in_window_rate,
    true_recovery_probability,
    window_factor,
)
from sim.config import load_ground_truth
from sim.outcomes import determine_recovery


def test_sim_and_eval_agree_on_the_attribution_window():
    assert_sim_agrees(load_ground_truth().attribution_window_days)


def test_the_sim_declaring_a_different_window_is_a_loud_failure():
    with pytest.raises(ValueError, match="attribution_window_days"):
        assert_sim_agrees(ATTRIBUTION_WINDOW_DAYS + 7)


def test_window_factor_is_the_share_of_scheduling_days_inside_the_window():
    gt = load_ground_truth()
    assert window_factor() == pytest.approx(ATTRIBUTION_WINDOW_DAYS / gt.recovery_scheduling_max_day)
    assert 0 < window_factor() <= 1


def test_holdout_probability_is_the_bare_natural_rate():
    gt = load_ground_truth()
    assert true_recovery_probability("balance_timing", "holdout") == gt.natural_recovery_rate["balance_timing"]


def test_treatment_probability_adds_the_uplift():
    gt = load_ground_truth()
    expected = gt.natural_recovery_rate["mandate_dead"] + gt.treatment_uplift["mandate_dead"]
    assert true_recovery_probability("mandate_dead", "treatment") == pytest.approx(expected)


def test_treatment_probability_is_clamped_the_same_way_sim_clamps_it():
    """determine_recovery caps at 0.95; a prediction that didn't would
    over-predict any cause whose natural rate plus uplift crosses it."""
    gt = load_ground_truth()
    for cause in gt.natural_recovery_rate:
        assert true_recovery_probability(cause, "treatment") <= MAX_RECOVERY_PROBABILITY


def test_expected_rate_uses_the_actual_case_mix_not_the_nominal_distribution():
    """A batch skewed towards one cause must be predicted on that skew —
    predicting from root_cause_distribution would be checking the
    measurement against a batch that wasn't generated."""
    gt = load_ground_truth()
    causes = ["mandate_dead"] * 3 + ["balance_timing"]
    expected = (
        3 * gt.natural_recovery_rate["mandate_dead"] + gt.natural_recovery_rate["balance_timing"]
    ) / 4 * window_factor()
    assert expected_in_window_rate(causes, "holdout") == pytest.approx(expected)


def test_expected_rate_of_an_empty_arm_is_none():
    assert expected_in_window_rate([], "treatment") is None


def test_check_passes_when_the_true_effect_falls_inside_the_interval():
    causes_t = ["balance_timing"] * 100
    causes_h = ["balance_timing"] * 100
    truth = expected_in_window_rate(causes_t, "treatment") - expected_in_window_rate(causes_h, "holdout")

    check = check_against_ground_truth(causes_t, causes_h, truth + 0.01, truth - 0.05, truth + 0.05)

    assert check.expected_within_ci is True
    assert check.absolute_error == pytest.approx(0.01)


def test_check_fails_when_the_true_effect_falls_outside_the_interval():
    causes = ["balance_timing"] * 100
    truth = expected_in_window_rate(causes, "treatment") - expected_in_window_rate(causes, "holdout")

    check = check_against_ground_truth(causes, causes, truth + 0.20, truth + 0.15, truth + 0.25)

    assert check.expected_within_ci is False


def test_check_reports_no_verdict_when_there_is_no_interval():
    causes = ["balance_timing"] * 10
    check = check_against_ground_truth(causes, causes, 0.1, None, None)
    assert check.expected_within_ci is None
    assert check.expected_incremental_rate is not None


def test_check_is_none_of_the_arms_have_no_known_true_cause():
    check = check_against_ground_truth(["balance_timing"], [], 0.1, 0.0, 0.2)
    assert check.expected_incremental_rate is None
    assert check.absolute_error is None


def test_nominal_uplift_exceeds_the_windowed_prediction():
    """The gap between the two is the attribution window doing its job —
    if they were equal, the window would be excluding nothing."""
    causes = ["balance_timing"] * 50 + ["stale_credential"] * 50
    check = check_against_ground_truth(causes, causes, 0.0, None, None)
    assert check.nominal_mean_uplift > check.expected_incremental_rate
    assert check.expected_incremental_rate == pytest.approx(check.nominal_mean_uplift * window_factor())


def test_measured_in_window_rate_converges_on_the_prediction():
    """The estimator's honesty test, independent of any one batch's luck:
    draw a large population straight from sim's outcome model, apply the
    report's attribution rule, and the measured per-arm rates and the
    measured lift all land on what eval/groundtruth.py predicts."""
    causes = ["balance_timing", "stale_credential", "issuer_soft_decline", "mandate_dead", "gateway_timeout"]
    n_per_cause = 4000
    population = [cause for cause in causes for _ in range(n_per_cause)]

    measured = {}
    for arm in ("treatment", "holdout"):
        in_window = 0
        for index, cause in enumerate(population):
            # Distinct seed per (arm, case) so the two arms aren't drawing
            # the same coin — mirroring distinct per-case seeds in a batch.
            plan = determine_recovery(cause, arm, seed=index * 2 + (0 if arm == "holdout" else 1))
            if plan.will_recover and plan.recovery_day <= ATTRIBUTION_WINDOW_DAYS:
                in_window += 1
        measured[arm] = in_window / len(population)

    predicted_treatment = expected_in_window_rate(population, "treatment")
    predicted_holdout = expected_in_window_rate(population, "holdout")

    assert measured["treatment"] == pytest.approx(predicted_treatment, abs=0.01)
    assert measured["holdout"] == pytest.approx(predicted_holdout, abs=0.01)
    assert (measured["treatment"] - measured["holdout"]) == pytest.approx(
        predicted_treatment - predicted_holdout, abs=0.015
    )
