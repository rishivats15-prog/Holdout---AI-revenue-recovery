"""sim/outcomes.py tests — the recovery-plan draw and the cosmetic touch
texture, each isolated from any orchestration.
"""

from __future__ import annotations

from sim.config import load_ground_truth
from sim.outcomes import determine_recovery, simulate_touch_texture


class StubRng:
    """Replays a fixed sequence of .random() results."""

    def __init__(self, values):
        self._values = iter(values)

    def random(self):
        return next(self._values)


def test_determine_recovery_is_deterministic_for_the_same_seed():
    first = determine_recovery("balance_timing", "treatment", seed=42)
    second = determine_recovery("balance_timing", "treatment", seed=42)
    assert first == second


def test_recovery_day_is_set_exactly_when_recovering():
    for seed in range(50):
        plan = determine_recovery("mandate_dead", "holdout", seed=seed)
        assert (plan.recovery_day is None) == (not plan.will_recover)


def test_recovery_day_stays_within_the_configured_scheduling_range():
    gt = load_ground_truth()
    found_one = False
    for seed in range(200):
        plan = determine_recovery("gateway_timeout", "treatment", seed=seed)
        if plan.will_recover:
            found_one = True
            assert 1 <= plan.recovery_day <= gt.recovery_scheduling_max_day
    assert found_one, "no recovering case found in 200 seeds — gateway_timeout's rate looks wrong"


def test_treatment_and_holdout_rates_converge_on_the_configured_ground_truth():
    """This is the single-draw model's core promise: across enough cases,
    the empirical rate per arm lands on natural_recovery_rate (holdout) and
    natural_recovery_rate + treatment_uplift (treatment) — not something
    inflated by compounding across touches. If this drifts, the eval
    sanity check in Phase 6 would too."""
    gt = load_ground_truth()
    cause = "stale_credential"
    n = 5000

    treatment_rate = sum(determine_recovery(cause, "treatment", seed=s).will_recover for s in range(n)) / n
    holdout_rate = sum(determine_recovery(cause, "holdout", seed=s).will_recover for s in range(n)) / n

    expected_treatment = gt.natural_recovery_rate[cause] + gt.treatment_uplift[cause]
    expected_holdout = gt.natural_recovery_rate[cause]

    assert abs(treatment_rate - expected_treatment) < 0.03
    assert abs(holdout_rate - expected_holdout) < 0.03
    assert treatment_rate > holdout_rate


def test_texture_is_nothing_when_not_delivered():
    assert simulate_touch_texture("sms", delivered=False, rng=StubRng([0.0, 0.0])) == "nothing"


def test_texture_is_nothing_on_a_non_human_channel_even_if_delivered():
    assert simulate_touch_texture("retry", delivered=True, rng=StubRng([0.0, 0.0])) == "nothing"


def test_texture_can_be_a_dispute_on_a_low_first_roll():
    assert simulate_touch_texture("sms", delivered=True, rng=StubRng([0.0])) == "dispute"


def test_texture_is_a_promise_when_the_dispute_roll_misses_but_the_promise_roll_hits():
    assert simulate_touch_texture("sms", delivered=True, rng=StubRng([0.99, 0.0])) == "promise_to_pay"


def test_texture_is_nothing_when_both_rolls_miss():
    assert simulate_touch_texture("sms", delivered=True, rng=StubRng([0.99, 0.99])) == "nothing"
