"""State and helpers for the live run page.

The run's own state (RNG, circuit breaker, tick counter) lives on an
orchestrator RunSession; this module just holds one of those in memory for
the server process and assembles what the page needs to render.

In-memory means: restarting the server abandons a run in progress. That is
a real limit, not a hidden one — the page says so, and the RUNBOOK repeats
it. The alternative would be persisting RNG state to the database, which
would be a lot of machinery for a single-process local demo.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from db.enums import CaseState, ExperimentArm, PolicyVerdict
from db.models import (
    Action,
    Case,
    CaseEvent,
    Customer,
    Diagnosis,
    ExperimentAssignment,
    Outcome,
    Playbook,
    Signal,
)
from diagnosis.diagnose import diagnose_all_detected
from eval.assignment import assign_experiment_arms
from orchestrator.session import RunSession
from sim.generator import generate_batch

DEFAULT_BATCH_SIZE = 900

# Ordered so the counter strip never reshuffles as numbers move through it —
# left to right is roughly the path a case takes.
STATE_ORDER = (
    CaseState.DETECTED.value,
    CaseState.DIAGNOSED.value,
    CaseState.IN_TREATMENT.value,
    CaseState.PAUSED.value,
    CaseState.RECOVERED.value,
    CaseState.HUMAN_QUEUE.value,
    CaseState.SUPPRESSED.value,
    CaseState.WRITTEN_OFF.value,
)

STATE_TONES = {
    CaseState.DETECTED.value: "neutral",
    CaseState.DIAGNOSED.value: "neutral",
    CaseState.IN_TREATMENT.value: "at-risk",
    CaseState.PAUSED.value: "at-risk",
    CaseState.RECOVERED.value: "recovered",
    CaseState.HUMAN_QUEUE.value: "veto",
    CaseState.SUPPRESSED.value: "veto",
    CaseState.WRITTEN_OFF.value: "neutral",
}

# Deleted child-first so foreign keys stay satisfied at every step.
TABLES_IN_DELETE_ORDER = (CaseEvent, Outcome, Action, ExperimentAssignment, Diagnosis, Signal, Case, Playbook, Customer)

_session: RunSession | None = None

# The run session is shared module state, but uvicorn runs sync endpoints on
# a threadpool — two overlapping posts (an impatient double-click, a stray
# second tab) would otherwise advance the same session twice concurrently,
# or tick a run while another request is deleting the rows underneath it.
# The second is not hypothetical: it surfaces as
# `StaleDataError: UPDATE on 'cases' expected to update 1 row(s); 0 matched`.
# Every route that mutates the run holds this lock, so the ticks serialize
# and the slower click simply waits its turn.
run_lock = threading.Lock()


def current_session() -> RunSession | None:
    return _session


def clear_session() -> None:
    global _session
    _session = None


def reset_database(db: Session) -> None:
    """Empties every table through the ORM rather than deleting the file.

    The FastAPI process holds an open connection to the SQLite file;
    unlinking it out from under the engine leaves the pooled connection
    pointing at a deleted inode. `make seed` can delete the file because
    it owns the process; the web app cannot.
    """
    for model in TABLES_IN_DELETE_ORDER:
        db.query(model).delete(synchronize_session=False)
    db.commit()
    # The bulk deletes above bypass the identity map, so anything this
    # session already loaded would still look present. Expire it all.
    db.expire_all()


def start_new_batch(db: Session, n_customers: int | None = None) -> RunSession:
    """Everything `make seed` does except running the ticks: generate the
    signals, detect cases, diagnose them, and randomize the arms. The run
    is then sitting at day zero with nothing treated yet — which is the
    point of watching it happen."""
    global _session

    # Resolved at call time rather than bound as a default, so the size can
    # be turned down for tests without regenerating 900 customers per case.
    n_customers = DEFAULT_BATCH_SIZE if n_customers is None else n_customers

    reset_database(db)
    generate_batch(db, n_customers=n_customers)
    diagnose_all_detected(db)
    assign_experiment_arms(db)

    _session = RunSession()
    return _session


@dataclass(frozen=True)
class StateCount:
    state: str
    count: int
    tone: str
    share: float


@dataclass(frozen=True)
class LiveSnapshot:
    session: RunSession | None
    total_cases: int
    states: tuple[StateCount, ...]
    treated_cases: int
    holdout_cases: int
    recovered_paise: int
    exposure_paise: int
    executed: int
    vetoed: int
    has_batch: bool

    @property
    def recovered_cases(self) -> int:
        return next((s.count for s in self.states if s.state == CaseState.RECOVERED.value), 0)

    @property
    def session_is_orphaned(self) -> bool:
        """A run is tracked but its cases are gone — the batch was cleared
        or regenerated elsewhere. Ticking on would write into an empty
        database, so the page offers a fresh batch instead."""
        return self.session is not None and not self.has_batch


def snapshot(db: Session) -> LiveSnapshot:
    session = current_session()
    cases = db.query(Case).all()
    total = len(cases)

    counts = {state: 0 for state in STATE_ORDER}
    for case in cases:
        counts[case.state] = counts.get(case.state, 0) + 1

    assignments = db.query(ExperimentAssignment).all()
    treated = sum(1 for row in assignments if row.arm == ExperimentArm.TREATMENT.value)

    actions = db.query(Action).all()
    payments = db.query(Outcome).filter(Outcome.outcome_type == "payment").all()

    return LiveSnapshot(
        session=session,
        total_cases=total,
        states=tuple(
            StateCount(
                state=state, count=counts.get(state, 0), tone=STATE_TONES.get(state, "neutral"),
                share=(counts.get(state, 0) / total) if total else 0.0,
            )
            for state in STATE_ORDER
        ),
        treated_cases=treated,
        holdout_cases=len(assignments) - treated,
        recovered_paise=sum(payment.amount or 0 for payment in payments),
        exposure_paise=sum(case.exposure_amount for case in cases),
        executed=sum(1 for a in actions if a.policy_verdict == PolicyVerdict.ALLOWED.value),
        vetoed=sum(1 for a in actions if a.policy_verdict == PolicyVerdict.VETOED.value),
        has_batch=total > 0,
    )
