"""Hash-chaining for case_events — tamper-evidence, built in from day one.

Each event's hash covers its own content plus the previous event's hash for
the same case, so altering or deleting a past row breaks every hash after
it. This is what makes case_events an audit trail rather than a log table.
"""

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import CaseEvent

GENESIS_HASH = "0" * 64


def compute_event_hash(case_id: int, event_type: str, payload: dict | None, prev_hash: str) -> str:
    canonical = json.dumps(
        {"case_id": case_id, "event_type": event_type, "payload": payload, "prev_hash": prev_hash},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_case_event(db: Session, case_id: int, event_type: str, payload: dict | None = None) -> CaseEvent:
    last = db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id.desc()).limit(1)
    ).scalar_one_or_none()
    prev_hash = last.hash if last else GENESIS_HASH
    event = CaseEvent(
        case_id=case_id,
        event_type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        hash=compute_event_hash(case_id, event_type, payload, prev_hash),
    )
    db.add(event)
    db.flush()
    return event


@dataclass(frozen=True)
class ChainVerification:
    """Result of re-walking one case's ledger. `broken_at_event_id` is the
    first row whose stored hash doesn't match a recomputation, or whose
    prev_hash doesn't match the row before it — everything after that
    point is untrustworthy."""

    case_id: int
    events: int
    intact: bool
    broken_at_event_id: int | None = None
    reason: str | None = None

    @property
    def label(self) -> str:
        return "VERIFIED" if self.intact else "BROKEN"


def verify_case_chain(db: Session, case_id: int) -> ChainVerification:
    """Recomputes every hash in a case's ledger and checks each row links
    to the one before it. This is what makes the audit trail
    tamper-EVIDENT rather than merely append-only: editing a payload or
    deleting a row anywhere in the chain surfaces here immediately."""
    events = db.execute(
        select(CaseEvent).where(CaseEvent.case_id == case_id).order_by(CaseEvent.id)
    ).scalars().all()

    expected_prev = GENESIS_HASH
    for event in events:
        if event.prev_hash != expected_prev:
            return ChainVerification(
                case_id=case_id, events=len(events), intact=False,
                broken_at_event_id=event.id, reason="prev_hash does not match the preceding event",
            )
        recomputed = compute_event_hash(event.case_id, event.event_type, event.payload, event.prev_hash)
        if recomputed != event.hash:
            return ChainVerification(
                case_id=case_id, events=len(events), intact=False,
                broken_at_event_id=event.id, reason="stored hash does not match a recomputation of the row",
            )
        expected_prev = event.hash

    return ChainVerification(case_id=case_id, events=len(events), intact=True)
