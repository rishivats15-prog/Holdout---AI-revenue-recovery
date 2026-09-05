"""eval/metrics.py — the pure statistics, checked against hand-computed
values. If these drift, every figure in the batch report drifts with them.
"""

from __future__ import annotations

import math

import pytest

from eval.metrics import mean, median, rate, two_proportion_ci


def test_two_proportion_ci_matches_a_hand_computed_interval():
    # p_a = 0.5, p_b = 0.3, delta = 0.2
    # se = sqrt(0.5*0.5/100 + 0.3*0.7/100) = sqrt(0.0046) = 0.0678233
    # margin = 1.96 * 0.0678233 = 0.1329337
    ci = two_proportion_ci(50, 100, 30, 100)
    assert ci.delta == pytest.approx(0.2)
    assert ci.lower == pytest.approx(0.2 - 0.1329337, abs=1e-6)
    assert ci.upper == pytest.approx(0.2 + 0.1329337, abs=1e-6)


def test_ci_widens_as_the_smaller_arm_shrinks():
    """The holdout is the binding constraint on precision — a 15% holdout
    buys a narrower interval than a 5% one at the same total case count."""
    wide = two_proportion_ci(300, 1000, 15, 50)
    narrow = two_proportion_ci(300, 1000, 90, 300)
    assert (wide.upper - wide.lower) > (narrow.upper - narrow.lower)


def test_ci_is_symmetric_around_the_delta():
    ci = two_proportion_ci(41, 137, 19, 83)
    assert (ci.upper - ci.delta) == pytest.approx(ci.delta - ci.lower)


def test_ci_of_identical_rates_straddles_zero():
    ci = two_proportion_ci(30, 100, 30, 100)
    assert ci.delta == 0.0
    assert ci.lower < 0 < ci.upper


def test_ci_is_zero_width_when_both_arms_are_degenerate():
    """Nobody recovered in either arm: the normal approximation has no
    variance to work with. It returns a point, not an error — the report
    shows that as an interval a reader can see is uninformative."""
    ci = two_proportion_ci(0, 40, 0, 40)
    assert ci.lower == ci.upper == 0.0


def test_ci_requires_both_arms_to_be_non_empty():
    with pytest.raises(ValueError):
        two_proportion_ci(5, 10, 0, 0)


def test_a_wider_z_gives_a_wider_interval():
    ci_95 = two_proportion_ci(50, 200, 30, 200)
    ci_99 = two_proportion_ci(50, 200, 30, 200, z=2.576)
    assert (ci_99.upper - ci_99.lower) > (ci_95.upper - ci_95.lower)


def test_rate_returns_zero_rather_than_dividing_by_zero():
    assert rate(0, 0) == 0.0
    assert rate(3, 12) == 0.25


def test_median_of_odd_and_even_length_and_empty():
    assert median([5.0, 1.0, 3.0]) == 3.0
    assert median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert median([]) is None


def test_mean_of_empty_is_none():
    assert mean([]) is None
    assert mean([1.0, 2.0, 6.0]) == 3.0


def test_se_formula_is_the_independent_two_sample_one():
    """Guards against someone 'simplifying' to a pooled-variance SE, which
    is the right formula for a null-hypothesis test and the wrong one for
    an interval around the observed difference."""
    ci = two_proportion_ci(60, 200, 40, 200)
    p_a, p_b = 0.3, 0.2
    expected_se = math.sqrt(p_a * (1 - p_a) / 200 + p_b * (1 - p_b) / 200)
    assert (ci.upper - ci.delta) == pytest.approx(1.96 * expected_se)
