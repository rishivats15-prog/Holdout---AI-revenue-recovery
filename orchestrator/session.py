"""One run of the pipeline, advanced a day at a time.

Phase 5's `run_orchestrator` used to own the per-run state — the RNG and
the circuit breaker — inside its own loop, which meant a run could only
ever happen all at once. That state now lives on a RunSession, so the
same run can be driven either straight through (the CLI's `make seed`) or
one tick per click (the dashboard's live run page).

There is deliberately only one implementation of "advance a day". The
live page is not a demo mode running a simplified copy of the
orchestrator — it calls exactly the function the batch runner calls, in
the same order, against the same database, with the same seeds. Stepping
21 days by hand therefore lands on a database identical to the one
`make seed` produces, and there is a test that fails if it ever doesn't.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from db.models import utcnow
from orchestrator.promises import resolve_due_promises
from orchestrator.recovery import resolve_scheduled_recoveries
from orchestrator.tick import run_tick
from policy.context import CircuitBreaker

RUNNER_RNG_SEED = 20260822  # independent of sim's generation seed and execute's channel-failure seed
DEFAULT_TICKS = 21

TICK_TOTAL_KEYS = (
    "executed", "vetoed", "routed", "written_off", "suppressed", "skipped_rungs", "paused", "disputed",
)
RUN_TOTAL_KEYS = TICK_TOTAL_KEYS + ("recovered", "recovered_while_paused", "promises_broken")


@dataclass(frozen=True)
class TickResult:
    """What one simulated day did. Kept per tick so the live run page can
    show the day's activity and the shape of the run so far."""

    tick_number: int
    day: dt.datetime
    executed: int
    vetoed: int
    routed: int
    written_off: int
    suppressed: int
    skipped_rungs: int
    paused: int
    disputed: int
    recovered: int
    recovered_while_paused: int
    promises_broken: int

    @property
    def day_label(self) -> str:
        return f"Day {self.tick_number + 1}"

    @property
    def is_quiet(self) -> bool:
        return not any(
            (self.executed, self.vetoed, self.recovered, self.paused, self.promises_broken, self.disputed)
        )


def default_start(now: dt.datetime | None = None) -> dt.datetime:
    return (now or utcnow()).replace(hour=12, minute=0, second=0, microsecond=0)


@dataclass
class RunSession:
    """The per-run state that has to survive between ticks.

    Held in memory. For the CLI that is the life of one process; for the
    dashboard it is the life of the server process, which is why
    restarting the server mid-run means starting the run over. That's an
    acceptable limit for a single-process local demo, and the RUNBOOK says
    so rather than leaving it to be discovered.
    """

    start: dt.datetime = field(default_factory=default_start)
    total_ticks: int = DEFAULT_TICKS
    tick_number: int = 0
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    rng: random.Random = field(default_factory=lambda: random.Random(RUNNER_RNG_SEED))
    history: list[TickResult] = field(default_factory=list)
    totals: dict = field(default_factory=lambda: {key: 0 for key in RUN_TOTAL_KEYS})

    @property
    def is_complete(self) -> bool:
        return self.tick_number >= self.total_ticks

    @property
    def days_remaining(self) -> int:
        return max(self.total_ticks - self.tick_number, 0)

    @property
    def progress_pct(self) -> float:
        return (self.tick_number / self.total_ticks * 100) if self.total_ticks else 0.0

    def advance(self, db: Session) -> TickResult:
        """One simulated day, in the order Phase 5 fixed:

          1. resolve_scheduled_recoveries — the only place a case ever
             becomes recovered, in either arm, from any state.
          2. resolve_due_promises — anyone past their grace deadline
             breaks and re-escalates one rung.
          3. run_tick — decide/execute/observe for treatment-arm cases.
        """
        now = self.start + dt.timedelta(days=self.tick_number)

        recovery = resolve_scheduled_recoveries(db, now)
        promises = resolve_due_promises(db, now)
        tick = run_tick(db, now, self.tick_number, self.circuit_breaker, self.rng)

        result = TickResult(
            tick_number=self.tick_number,
            day=now,
            recovered=recovery["recovered"],
            recovered_while_paused=recovery["recovered_while_paused"],
            promises_broken=promises["promises_broken"],
            **{key: tick[key] for key in TICK_TOTAL_KEYS},
        )

        for key in RUN_TOTAL_KEYS:
            self.totals[key] += getattr(result, key)
        self.history.append(result)
        self.tick_number += 1
        return result

    def advance_days(self, db: Session, days: int) -> list[TickResult]:
        """Stops at the end of the run rather than ticking past it."""
        return [self.advance(db) for _ in range(min(days, self.days_remaining))]

    def run_totals(self) -> dict:
        return {**self.totals, "ticks": self.tick_number, "circuit_breaker_tripped": self.circuit_breaker.is_open()}
