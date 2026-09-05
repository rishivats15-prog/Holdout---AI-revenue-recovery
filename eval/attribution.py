"""The attribution window — the single rule that decides which payments
count as recoveries.

Fixed at 14 days from detection (CLAUDE.md). It lives here, in /eval,
rather than being read out of sim/ground_truth.yaml, because it is a
*measurement* policy, not a property of the synthetic world: it would be
exactly 14 days against a real gateway too. The sim declares the same
number so it can deliberately schedule some recoveries beyond it; the
check below fails loudly if the two ever drift apart, because a sim that
schedules against one window while eval measures against another would
silently bias every rate in the report.
"""

from __future__ import annotations

import datetime as dt

ATTRIBUTION_WINDOW_DAYS = 14


def is_within_window(detected_at: dt.datetime, occurred_at: dt.datetime, window_days: int = ATTRIBUTION_WINDOW_DAYS) -> bool:
    """Inclusive of the boundary day. A payment before detection can't be a
    recovery of this case, so it's excluded too."""
    delta = occurred_at - detected_at
    return dt.timedelta(0) <= delta <= dt.timedelta(days=window_days)


def days_to_cash(detected_at: dt.datetime, occurred_at: dt.datetime) -> int:
    return (occurred_at - detected_at).days


def assert_sim_agrees(sim_window_days: int) -> None:
    if sim_window_days != ATTRIBUTION_WINDOW_DAYS:
        raise ValueError(
            f"sim/ground_truth.yaml declares attribution_window_days={sim_window_days} but /eval measures "
            f"against {ATTRIBUTION_WINDOW_DAYS}. These must match — otherwise the ground-truth check in "
            "eval/groundtruth.py compares a predicted rate computed over one window against a measured rate "
            "counted over another, and every number in the report inherits that bias."
        )
