"""Simulates the synthetic world's ground truth in action — the only place
a case actually recovers, and the only place a treatment touch's cosmetic
texture (a promise-to-pay conversation, a complaint) gets decided.

Nothing in diagnose/decide/policy/orchestrator reads sim/ground_truth.yaml
or a case's true_root_cause directly; the orchestrator only calls these
functions and acts on their result, the same way it would call out to a
real payment gateway and a real customer if either existed.

Whether a case recovers at all is decided ONCE per case, not re-rolled on
every touch. A per-touch roll compounds across a multi-rung ladder and
would inflate the measured rate well past natural_recovery_rate /
treatment_uplift, making Phase 6's ground-truth sanity check meaningless.
Deciding once, from a case's own stored experiment-assignment seed, means
the arms' cumulative recovery rates converge on the exact YAML numbers as
the batch grows — so the measured incremental lift is an honest estimate
of treatment_uplift, not an artifact of how many rungs a ladder happens
to have.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from sim.config import load_ground_truth

HUMAN_FACING_CHANNELS = frozenset({"sms", "whatsapp", "voice"})


@dataclass(frozen=True)
class RecoveryPlan:
    will_recover: bool
    recovery_day: int | None  # days since case.detected_at; None if will_recover is False


def determine_recovery(true_cause: str, arm: str, seed: int) -> RecoveryPlan:
    """The one draw that decides a case's fate, deterministically from its
    stored per-case seed — calling this twice for the same inputs always
    returns the same plan, so it never needs to be persisted separately.

    Uses the case's TRUE root cause, not the diagnosed one: whether a case
    actually recovers is a property of reality, not of what the diagnosis
    engine guessed. This is the one place in the system that's allowed to
    read it — see the note on Case.true_root_cause in db/models.py.

    The uplift is applied from `arm` alone, never from whether a touch
    actually landed. See the treatment_uplift note in ground_truth.yaml:
    that's what makes the holdout comparison an intent-to-treat estimate,
    which is the only kind a randomized holdout can support.
    """
    gt = load_ground_truth()
    rng = random.Random(seed)

    base = gt.natural_recovery_rate[true_cause]
    p = min(base + gt.treatment_uplift[true_cause], 0.95) if arm == "treatment" else base

    will_recover = rng.random() < p
    if not will_recover:
        return RecoveryPlan(will_recover=False, recovery_day=None)
    return RecoveryPlan(will_recover=True, recovery_day=rng.randint(1, gt.recovery_scheduling_max_day))


def simulate_touch_texture(channel: str | None, delivered: bool, rng: random.Random) -> str:
    """What a delivered, human-facing touch prompts, if anything —
    cosmetic timeline texture that never affects whether or when the case
    actually recovers (determine_recovery already fixed that). Returns
    "dispute", "promise_to_pay", or "nothing". A dispute and a promise are
    mutually exclusive outcomes of the same touch.
    """
    if not delivered or channel not in HUMAN_FACING_CHANNELS:
        return "nothing"

    gt = load_ground_truth()
    if rng.random() < gt.dispute_rate:
        return "dispute"
    if rng.random() < gt.promise_prompt_rate:
        return "promise_to_pay"
    return "nothing"
