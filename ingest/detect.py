"""Detect step — pure rules, no LLM. Folds signals into cases, deduping hard
so a customer's retries within one open episode become a single case rather
than three.

Dedup key is (customer_id, lane, not-yet-terminal case) — a customer's
failures within one lane are one billing/collection episode until that
episode resolves (or is written off / suppressed), at which point a new
failure legitimately opens a new case.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from db.enums import CaseState
from db.hashchain import append_case_event
from db.models import Case, Signal

TERMINAL_STATES = {CaseState.RECOVERED.value, CaseState.WRITTEN_OFF.value, CaseState.SUPPRESSED.value}


def detect_from_signal(db: Session, signal: Signal) -> Case:
    case = _find_open_case(db, signal)
    if case is None:
        case = _open_case(db, signal)
    else:
        _fold_into_case(db, case, signal)

    signal.case_id = case.id
    db.flush()
    return case


def _find_open_case(db: Session, signal: Signal) -> Case | None:
    return (
        db.query(Case)
        .filter(
            Case.customer_id == signal.customer_id,
            Case.lane == signal.lane,
            Case.state.notin_(TERMINAL_STATES),
        )
        .order_by(Case.detected_at.desc())
        .first()
    )


def _open_case(db: Session, signal: Signal) -> Case:
    case = Case(
        customer_id=signal.customer_id,
        lane=signal.lane,
        state=CaseState.DETECTED.value,
        exposure_amount=signal.exposure_amount,
        detected_at=signal.occurred_at,
        true_root_cause=signal.raw_payload.get("_ground_truth_cause"),
    )
    db.add(case)
    db.flush()
    append_case_event(db, case.id, "case_detected", {"signal_id": signal.id, "source": signal.source})
    return case


def _fold_into_case(db: Session, case: Case, signal: Signal) -> None:
    case.exposure_amount = max(case.exposure_amount, signal.exposure_amount)
    db.flush()
    append_case_event(db, case.id, "signal_folded", {"signal_id": signal.id, "source": signal.source})
