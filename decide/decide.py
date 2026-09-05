"""Decide step — looks up the ordered treatment plan for a case's cause and
current rung. Rules-first, no LLM: every proposal traces back to a rung in
the lane's playbook YAML, never to a branch written in this module.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from db.enums import CaseState
from db.hashchain import append_case_event
from db.models import Case, Diagnosis, Playbook
from decide.ladder import Rung, load_playbook, next_rung


@dataclass(frozen=True)
class ProposedAction:
    case_id: int
    playbook_id: int
    cause: str
    ladder_rung: int
    action_type: str
    channel: str | None
    terminal_state: str | None
    cooldown_minutes: int
    cost_paise: int
    copy_template: str | None
    waiver_offer_paise: int
    # Set by nothing yet — a hook for Phase 7's copy-generation LLM call so
    # the content-rules policy check has real text to scan once that exists.
    copy_text: str | None = None


def decide_case(db: Session, case: Case) -> ProposedAction | None:
    diagnosis = _latest_diagnosis(db, case)
    rung = next_rung(case.lane, diagnosis.cause, case.ladder_rung)
    if rung is None:
        return None

    playbook_row = _ensure_playbook_row(db, case.lane)
    proposal = ProposedAction(
        case_id=case.id,
        playbook_id=playbook_row.id,
        cause=diagnosis.cause,
        ladder_rung=rung.rung,
        action_type=rung.action_type,
        channel=rung.channel,
        terminal_state=rung.terminal_state,
        cooldown_minutes=rung.cooldown_minutes,
        cost_paise=rung.cost_paise,
        copy_template=rung.copy_template,
        waiver_offer_paise=rung.waiver_offer_paise,
    )

    if rung.channel is None:
        case.state = rung.terminal_state
        append_case_event(
            db, case.id, "case_routed",
            {"action_type": rung.action_type, "terminal_state": rung.terminal_state, "cause": diagnosis.cause},
        )
    else:
        if case.state == CaseState.DIAGNOSED.value:
            case.state = CaseState.IN_TREATMENT.value
        append_case_event(
            db, case.id, "action_proposed",
            {
                "ladder_rung": rung.rung,
                "action_type": rung.action_type,
                "channel": rung.channel,
                "cause": diagnosis.cause,
            },
        )

    db.flush()
    return proposal


def decide_all_diagnosed(db: Session) -> list[ProposedAction]:
    """Runs decide for every case currently sitting in 'diagnosed'. Returns
    the proposed actions (one per case whose ladder wasn't already
    exhausted at rung 0, which never happens on a fresh diagnosis)."""
    cases = db.query(Case).filter(Case.state == CaseState.DIAGNOSED.value).all()
    proposals = [p for case in cases if (p := decide_case(db, case)) is not None]
    db.commit()
    return proposals


def _latest_diagnosis(db: Session, case: Case) -> Diagnosis:
    return (
        db.query(Diagnosis)
        .filter(Diagnosis.case_id == case.id)
        .order_by(Diagnosis.created_at.desc())
        .first()
    )


def _ensure_playbook_row(db: Session, lane: str) -> Playbook:
    playbook = load_playbook(lane)
    row = (
        db.query(Playbook)
        .filter(Playbook.lane == lane, Playbook.version == playbook.version)
        .one_or_none()
    )
    if row is None:
        row = Playbook(
            lane=playbook.lane,
            name=playbook.name,
            version=playbook.version,
            source_path=playbook.source_path,
            content=playbook.raw_content,
            is_active=True,
        )
        db.add(row)
        db.flush()
    return row
