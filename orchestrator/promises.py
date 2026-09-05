"""Resolves promise-to-pay cases whose promised-date-plus-grace-window has
elapsed. By the time this runs each tick, resolve_scheduled_recoveries has
already caught any case whose pre-determined recovery day arrived — which
is what "the promise was kept" really means here. So any case still
paused past its grace deadline is, by construction, broken: payment
hadn't landed by then. It re-enters treatment one rung higher, not from
scratch — the natural consequence of the state machine paused exists for,
not a bolted-on special case. It may still recover later if its scheduled
day is further out; resolve_scheduled_recoveries checks every state, not
just paused, so that isn't lost.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from db.enums import CaseState
from db.hashchain import append_case_event
from db.models import Case


def resolve_due_promises(db: Session, now: dt.datetime) -> dict:
    due = db.query(Case).filter(Case.state == CaseState.PAUSED.value, Case.paused_until <= now).all()

    for case in due:
        # execute_proposal already advanced ladder_rung past the rung that
        # led to this pause (routine "this rung is consumed" bookkeeping
        # on every send, pause or not). Adding one more here is the actual
        # "one rung higher" escalation: a broken promise skips the rung
        # normal progression would try next, rather than resetting to 0.
        case.ladder_rung += 1
        case.state = CaseState.IN_TREATMENT.value
        append_case_event(db, case.id, "promise_broken", {"new_ladder_rung": case.ladder_rung})

    db.commit()
    return {"promises_broken": len(due)}
