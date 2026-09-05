"""One orchestrator tick — a simulated day. Applies only to TREATMENT-arm
cases (holdout gets no outreach at all — resolve_scheduled_recoveries is
the only thing that ever touches them). For every treatable case ready
for a decision: decide the next rung, run it through the policy gate +
channel adapter, then either observe the touch's cosmetic texture (a
promise-to-pay conversation, a complaint — never whether the case
recovers; that's already fixed by resolve_scheduled_recoveries) or
classify the veto.

A veto is either:
  - transient (quiet hours, cooldown, frequency cap, kill switch, circuit
    breaker) — the case just waits and retries the same rung next tick;
  - permanently terminal for the case (cost ceiling / EV floor exceeded ->
    written_off; DND -> suppressed, matching "opt-out" in the state
    machine's own description);
  - stuck — the same rung has now been vetoed several times running for a
    case-specific reason (e.g. an unregistered template). Rather than
    loop forever, the rung is skipped.
"""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy.orm import Session

from actions.execute import run_batch
from db.enums import CaseState, ExperimentArm
from db.hashchain import append_case_event
from db.models import Action, Case, ExperimentAssignment, Outcome
from decide.decide import ProposedAction, decide_case
from decide.ladder import load_playbook
from policy import reasons
from policy.context import CircuitBreaker
from sim.config import load_ground_truth
from sim.outcomes import simulate_touch_texture

PENDING_STATES = frozenset({CaseState.DIAGNOSED.value, CaseState.IN_TREATMENT.value})

WRITE_OFF_REASONS = frozenset({reasons.COST_CEILING_EXCEEDED, reasons.EXPECTED_VALUE_BELOW_COST})
SUPPRESS_REASONS = frozenset({reasons.CUSTOMER_DND})
# System-wide halts aren't this case's fault — excluded from the stuck-rung
# count below so a kill-switch window spanning a few ticks can't spuriously
# skip a rung that was never actually the problem.
GLOBAL_HALT_REASONS = frozenset({reasons.KILL_SWITCH_ENGAGED, reasons.CIRCUIT_BREAKER_OPEN})
MAX_ATTEMPTS_PER_RUNG = 3


def run_tick(db: Session, now: dt.datetime, tick_number: int, circuit_breaker: CircuitBreaker, rng: random.Random) -> dict:
    totals = dict(
        executed=0, vetoed=0, routed=0, written_off=0, suppressed=0,
        skipped_rungs=0, paused=0, disputed=0,
    )

    cases = db.query(Case).filter(Case.state.in_(PENDING_STATES)).all()
    cases = _treatment_arm_only(db, cases)

    proposals = []
    for case in cases:
        proposal = decide_case(db, case)
        if proposal is None:
            case.state = CaseState.WRITTEN_OFF.value
            append_case_event(db, case.id, "case_written_off", {"reason": "ladder_exhausted"})
            totals["written_off"] += 1
            continue
        if proposal.channel is None:
            totals["routed"] += 1  # decide_case already routed it (e.g. risk_fraud -> human_queue)
            continue
        proposals.append(proposal)
    db.commit()

    execution = run_batch(db, proposals, now=now, tick_number=tick_number, circuit_breaker=circuit_breaker)
    totals["executed"] += execution["allowed"]
    totals["vetoed"] += execution["vetoed"]

    case_by_id = {case.id: case for case in cases}
    for action, proposal in zip(execution["actions"], proposals):
        case = case_by_id[action.case_id]
        if action.policy_verdict == "allowed":
            _observe_texture(db, case, action, proposal, now, rng, totals)
        else:
            _handle_veto(db, case, action, totals)

    db.commit()
    return totals


def _treatment_arm_only(db: Session, cases: list[Case]) -> list[Case]:
    """Holdout-arm cases never enter decide/execute — no outreach at all
    is the whole point of a holdout. A case with no assignment yet
    defaults to treatable, matching pre-assignment behaviour."""
    if not cases:
        return []
    assignments = {
        row.case_id: row
        for row in db.query(ExperimentAssignment).filter(ExperimentAssignment.case_id.in_([c.id for c in cases])).all()
    }
    return [c for c in cases if assignments.get(c.id) is None or assignments[c.id].arm == ExperimentArm.TREATMENT.value]


def _handle_veto(db: Session, case: Case, action: Action, totals: dict) -> None:
    if action.veto_reason_code in WRITE_OFF_REASONS:
        case.state = CaseState.WRITTEN_OFF.value
        append_case_event(db, case.id, "case_written_off", {"reason": action.veto_reason_code})
        totals["written_off"] += 1
        return

    if action.veto_reason_code in SUPPRESS_REASONS:
        case.state = CaseState.SUPPRESSED.value
        append_case_event(db, case.id, "case_suppressed", {"reason": action.veto_reason_code})
        totals["suppressed"] += 1
        return

    if action.veto_reason_code in GLOBAL_HALT_REASONS:
        return  # system-wide halt; wait it out, don't count against this case

    attempts_at_this_rung = (
        db.query(Action)
        .filter(
            Action.case_id == case.id,
            Action.ladder_rung == case.ladder_rung,
            Action.policy_verdict == "vetoed",
            Action.veto_reason_code.notin_(GLOBAL_HALT_REASONS),
        )
        .count()
    )
    if attempts_at_this_rung >= MAX_ATTEMPTS_PER_RUNG:
        skipped_rung = case.ladder_rung
        case.ladder_rung += 1
        db.flush()
        append_case_event(
            db, case.id, "rung_skipped",
            {"ladder_rung": skipped_rung, "reason": action.veto_reason_code, "attempts": attempts_at_this_rung},
        )
        totals["skipped_rungs"] += 1
    # else: transient — stays in_treatment at the same rung, retried next tick


def _observe_texture(
    db: Session, case: Case, action: Action, proposal: ProposedAction, now: dt.datetime, rng: random.Random, totals: dict
) -> None:
    """Never decides whether the case recovers — resolve_scheduled_recoveries
    already fixed that when the case was first assigned. Only decides the
    cosmetic shape of this particular touch's aftermath."""
    texture = simulate_touch_texture(proposal.channel, bool(action.delivered), rng)

    if texture == "dispute":
        case.state = CaseState.HUMAN_QUEUE.value
        db.add(Outcome(case_id=case.id, action_id=action.id, outcome_type="dispute", occurred_at=now))
        append_case_event(db, case.id, "case_disputed", {"action_id": action.id})
        totals["disputed"] += 1
    elif texture == "promise_to_pay":
        gt = load_ground_truth()
        promise_days = rng.randint(gt.promise_days_min, gt.promise_days_max)
        promised_date = now + dt.timedelta(days=promise_days)
        grace_days = load_playbook(case.lane).promise_to_pay_grace_days

        case.state = CaseState.PAUSED.value
        case.paused_until = promised_date + dt.timedelta(days=grace_days)
        db.add(Outcome(case_id=case.id, action_id=action.id, outcome_type="ptp", promised_date=promised_date, occurred_at=now))
        append_case_event(
            db, case.id, "case_paused",
            {"action_id": action.id, "promised_date": promised_date.isoformat(), "paused_until": case.paused_until.isoformat()},
        )
        totals["paused"] += 1
    # else "nothing": case stays in_treatment; execute_proposal already
    # advanced ladder_rung, so the next tick proposes the next rung.
