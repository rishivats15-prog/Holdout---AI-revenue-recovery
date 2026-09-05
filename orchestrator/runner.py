"""Runs the case-management pipeline forward in simulated time. Detect,
diagnose, and experiment assignment all happen at seed time before this is
called; this drives the run to completion, one tick (one simulated day) at
a time.

The per-tick work and the state that has to survive between ticks both
live in orchestrator/session.py, because the dashboard's live run page
advances the same run one day per click. This function is now just "make
a session and exhaust it" — keeping a single implementation of a day,
shared by the batch path and the interactive one.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from orchestrator.session import DEFAULT_TICKS, RUNNER_RNG_SEED, RunSession

__all__ = ["run_orchestrator", "RunSession", "RUNNER_RNG_SEED", "DEFAULT_TICKS"]


def run_orchestrator(db: Session, n_ticks: int = DEFAULT_TICKS, start: dt.datetime | None = None) -> dict:
    session = RunSession(total_ticks=n_ticks, **({"start": start} if start else {}))
    while not session.is_complete:
        session.advance(db)
    return session.run_totals()
