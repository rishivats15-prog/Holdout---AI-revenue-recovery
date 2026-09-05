"""Resolves any case whose pre-determined recovery day (sim/outcomes.py's
determine_recovery) has arrived. Runs first, every tick, before anything
else touches a case — this is the ONE place a case ever becomes
`recovered`; nothing else in the orchestrator sets that state.

Checks every case not already recovered, regardless of its current
state — including written_off, human_queue, and suppressed. A customer
can still pay after the business has internally given up on them; the
measurement should reflect what actually happened, not what the case
management system's state happened to say at the time.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from db.enums import CaseState
from db.hashchain import append_case_event
from db.models import Case, ExperimentAssignment, Outcome
from sim.outcomes import determine_recovery

PENDING_STATES = frozenset(s.value for s in CaseState) - {CaseState.RECOVERED.value}


def resolve_scheduled_recoveries(db: Session, now: dt.datetime) -> dict:
    cases = db.query(Case).filter(Case.state.in_(PENDING_STATES)).all()
    if not cases:
        return {"recovered": 0, "recovered_while_paused": 0}

    case_ids = [c.id for c in cases]
    assignments = {
        row.case_id: row
        for row in db.query(ExperimentAssignment).filter(ExperimentAssignment.case_id.in_(case_ids)).all()
    }

    recovered = 0
    recovered_while_paused = 0
    for case in cases:
        assignment = assignments.get(case.id)
        if assignment is None:
            continue  # not yet assigned an arm — nothing to resolve against

        plan = determine_recovery(case.true_root_cause, assignment.arm, assignment.seed)
        if not plan.will_recover:
            continue

        days_since_detection = (now - case.detected_at).days
        if days_since_detection < plan.recovery_day:
            continue

        prior_state = case.state
        occurred_at = case.detected_at + dt.timedelta(days=plan.recovery_day)
        case.state = CaseState.RECOVERED.value
        db.add(
            Outcome(case_id=case.id, outcome_type="payment", amount=case.exposure_amount, occurred_at=occurred_at)
        )
        append_case_event(
            db, case.id, "case_recovered",
            {"arm": assignment.arm, "recovery_day": plan.recovery_day, "prior_state": prior_state},
        )
        recovered += 1
        if prior_state == CaseState.PAUSED.value:
            recovered_while_paused += 1

    db.commit()
    return {"recovered": recovered, "recovered_while_paused": recovered_while_paused}
