"""Server-side list queries for the case queue and the veto log.

Filtering, sorting and paging are all SQL driven by FastAPI query params —
there is no client-side table JavaScript, so every list view is a plain
URL that can be linked, bookmarked, and rendered with scripting off.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from db.enums import CaseState, PolicyVerdict
from db.models import Action, Case, Customer, Diagnosis, ExperimentAssignment

PAGE_SIZE = 60

CASE_SORTS = {
    "exposure": (Case.exposure_amount.desc(), "Exposure"),
    "recent": (Case.detected_at.desc(), "Most recent"),
    "id": (Case.id.asc(), "Case id"),
}

# Which semantic colour a case state carries in a row's leading bar.
STATE_TONES = {
    CaseState.RECOVERED.value: "recovered",
    CaseState.SUPPRESSED.value: "veto",
    CaseState.HUMAN_QUEUE.value: "veto",
    CaseState.PAUSED.value: "at-risk",
    CaseState.IN_TREATMENT.value: "at-risk",
    CaseState.DIAGNOSED.value: "neutral",
    CaseState.DETECTED.value: "neutral",
    CaseState.WRITTEN_OFF.value: "neutral",
}


@dataclass(frozen=True)
class CaseRow:
    case: Case
    customer: Customer
    cause: str
    arm: str
    touches: int
    vetoes: int

    @property
    def tone(self) -> str:
        return STATE_TONES.get(self.case.state, "neutral")


@dataclass(frozen=True)
class VetoRow:
    action: Action
    case: Case
    customer: Customer
    cause: str


@dataclass(frozen=True)
class Page:
    rows: list
    total: int
    offset: int
    page_size: int

    @property
    def shown(self) -> int:
        return len(self.rows)

    @property
    def has_prev(self) -> bool:
        return self.offset > 0

    @property
    def has_next(self) -> bool:
        return self.offset + self.page_size < self.total

    @property
    def prev_offset(self) -> int:
        return max(self.offset - self.page_size, 0)

    @property
    def next_offset(self) -> int:
        return self.offset + self.page_size


def case_queue(
    db: Session,
    *,
    state: str | None = None,
    cause: str | None = None,
    arm: str | None = None,
    sort: str = "exposure",
    offset: int = 0,
    page_size: int = PAGE_SIZE,
) -> Page:
    query = (
        db.query(Case, Customer, Diagnosis, ExperimentAssignment)
        .join(Customer, Customer.id == Case.customer_id)
        .outerjoin(Diagnosis, Diagnosis.case_id == Case.id)
        .outerjoin(ExperimentAssignment, ExperimentAssignment.case_id == Case.id)
    )
    if state:
        query = query.filter(Case.state == state)
    if cause:
        query = query.filter(Diagnosis.cause == cause)
    if arm:
        query = query.filter(ExperimentAssignment.arm == arm)

    total = query.count()
    order_by = CASE_SORTS.get(sort, CASE_SORTS["exposure"])[0]
    records = query.order_by(order_by).offset(offset).limit(page_size).all()

    case_ids = [case.id for case, _, _, _ in records]
    counts = _action_counts(db, case_ids)

    rows = [
        CaseRow(
            case=case,
            customer=customer,
            cause=diagnosis.cause if diagnosis else "—",
            arm=assignment.arm if assignment else "—",
            touches=counts.get(case.id, (0, 0))[0],
            vetoes=counts.get(case.id, (0, 0))[1],
        )
        for case, customer, diagnosis, assignment in records
    ]
    return Page(rows=rows, total=total, offset=offset, page_size=page_size)


def _action_counts(db: Session, case_ids: list[int]) -> dict[int, tuple[int, int]]:
    if not case_ids:
        return {}
    counts: dict[int, tuple[int, int]] = {}
    for action in db.query(Action).filter(Action.case_id.in_(case_ids)).all():
        executed, vetoed = counts.get(action.case_id, (0, 0))
        if action.policy_verdict == PolicyVerdict.ALLOWED.value:
            counts[action.case_id] = (executed + 1, vetoed)
        else:
            counts[action.case_id] = (executed, vetoed + 1)
    return counts


def veto_log(
    db: Session, *, reason_code: str | None = None, offset: int = 0, page_size: int = PAGE_SIZE
) -> Page:
    query = (
        db.query(Action, Case, Customer, Diagnosis)
        .join(Case, Case.id == Action.case_id)
        .join(Customer, Customer.id == Case.customer_id)
        .outerjoin(Diagnosis, Diagnosis.case_id == Case.id)
        .filter(Action.policy_verdict == PolicyVerdict.VETOED.value)
    )
    if reason_code:
        query = query.filter(Action.veto_reason_code == reason_code)

    total = query.count()
    records = query.order_by(Action.created_at.desc(), Action.id.desc()).offset(offset).limit(page_size).all()

    rows = [
        VetoRow(action=action, case=case, customer=customer, cause=diagnosis.cause if diagnosis else "—")
        for action, case, customer, diagnosis in records
    ]
    return Page(rows=rows, total=total, offset=offset, page_size=page_size)


def distinct_states(db: Session) -> list[tuple[str, int]]:
    return _distinct(db, Case.state, Case)


def distinct_causes(db: Session) -> list[tuple[str, int]]:
    return _distinct(db, Diagnosis.cause, Diagnosis)


def _distinct(db: Session, column, model) -> list[tuple[str, int]]:
    from sqlalchemy import func

    rows = db.query(column, func.count(model.id)).group_by(column).all()
    return sorted(((value, count) for value, count in rows if value), key=lambda item: -item[1])
