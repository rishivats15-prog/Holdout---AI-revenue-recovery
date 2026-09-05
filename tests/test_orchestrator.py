"""Orchestrator tests: scheduled-recovery resolution (across arms and
states), promise-to-pay breaking, and run_tick's classification of vetoes
and touch texture into the right case-machine transition.
"""

from __future__ import annotations

import datetime as dt
import random

from actions.execute import execute_proposal
from db.enums import CaseState
from db.models import Case, Customer, Diagnosis, ExperimentAssignment, Outcome
from decide.decide import decide_case
from decide.ladder import load_playbook
from orchestrator import tick as tick_module
from orchestrator.promises import resolve_due_promises
from orchestrator.recovery import resolve_scheduled_recoveries
from orchestrator.tick import run_tick
from policy.context import CircuitBreaker
from sim.outcomes import determine_recovery

NOON = dt.datetime(2026, 1, 5, 12, 0)  # naive UTC, matching db.models.utcnow

_customer_counter = 0


def _make_treatable_case(
    db_session, cause: str, ladder_rung: int = 0, state: str = CaseState.IN_TREATMENT.value,
    customer_overrides=None, detected_at: dt.datetime | None = None,
) -> tuple[Case, Customer]:
    global _customer_counter
    _customer_counter += 1
    defaults = dict(external_ref=f"cust_orc_{_customer_counter}", phone="9000000000", dnd=False, whatsapp_opt_in=True)
    defaults.update(customer_overrides or {})
    customer = Customer(**defaults)
    db_session.add(customer)
    db_session.flush()

    case = Case(
        customer_id=customer.id, lane="subscription", state=state,
        exposure_amount=1_000_000, detected_at=detected_at or NOON, ladder_rung=ladder_rung,
        true_root_cause=cause,
    )
    db_session.add(case)
    db_session.flush()

    db_session.add(Diagnosis(case_id=case.id, cause=cause, confidence=1.0, method="rule", evidence={}))
    db_session.flush()
    return case, customer


def _find_seed(cause: str, arm: str, want_recover: bool, min_recovery_day: int | None = None, max_tries: int = 2000):
    for seed in range(max_tries):
        plan = determine_recovery(cause, arm, seed)
        if plan.will_recover != want_recover:
            continue
        if want_recover and min_recovery_day is not None and plan.recovery_day < min_recovery_day:
            continue
        return seed, plan
    raise AssertionError(f"no seed found for {cause}/{arm} want_recover={want_recover} min_recovery_day={min_recovery_day}")


def _assign(db_session, case: Case, arm: str, seed: int) -> None:
    db_session.add(ExperimentAssignment(case_id=case.id, arm=arm, stratum="test", seed=seed))
    db_session.flush()


# --- scheduled recovery ------------------------------------------------


def test_scheduled_recovery_fires_once_the_day_arrives(db_session):
    seed, plan = _find_seed("balance_timing", "treatment", want_recover=True)
    detected_at = NOON - dt.timedelta(days=plan.recovery_day)  # already due as of NOON
    case, _ = _make_treatable_case(db_session, "balance_timing", detected_at=detected_at)
    _assign(db_session, case, "treatment", seed)

    result = resolve_scheduled_recoveries(db_session, NOON)

    assert result["recovered"] == 1
    assert case.state == CaseState.RECOVERED.value
    outcome = db_session.query(Outcome).filter(Outcome.case_id == case.id).one()
    assert outcome.outcome_type == "payment"
    assert outcome.amount == case.exposure_amount


def test_scheduled_recovery_does_not_fire_before_its_day(db_session):
    seed, plan = _find_seed("balance_timing", "treatment", want_recover=True, min_recovery_day=2)
    detected_at = NOON - dt.timedelta(days=plan.recovery_day - 1)  # one day short of due
    case, _ = _make_treatable_case(db_session, "balance_timing", detected_at=detected_at)
    _assign(db_session, case, "treatment", seed)

    result = resolve_scheduled_recoveries(db_session, NOON)

    assert result["recovered"] == 0
    assert case.state == CaseState.IN_TREATMENT.value


def test_scheduled_recovery_still_fires_for_a_written_off_case(db_session):
    """A customer can still pay after the business has internally given up
    on them — the measurement should reflect what actually happened."""
    seed, plan = _find_seed("balance_timing", "treatment", want_recover=True)
    detected_at = NOON - dt.timedelta(days=plan.recovery_day)
    case, _ = _make_treatable_case(
        db_session, "balance_timing", detected_at=detected_at, state=CaseState.WRITTEN_OFF.value
    )
    _assign(db_session, case, "treatment", seed)

    result = resolve_scheduled_recoveries(db_session, NOON)

    assert result["recovered"] == 1
    assert case.state == CaseState.RECOVERED.value


def test_a_case_that_will_never_recover_is_left_alone(db_session):
    seed, _ = _find_seed("risk_fraud", "holdout", want_recover=False)
    case, _ = _make_treatable_case(db_session, "risk_fraud", detected_at=NOON - dt.timedelta(days=19))
    _assign(db_session, case, "holdout", seed)

    result = resolve_scheduled_recoveries(db_session, NOON)

    assert result["recovered"] == 0
    assert case.state == CaseState.IN_TREATMENT.value


def test_recovered_while_paused_is_tracked_separately(db_session):
    seed, plan = _find_seed("balance_timing", "treatment", want_recover=True)
    detected_at = NOON - dt.timedelta(days=plan.recovery_day)
    case, _ = _make_treatable_case(
        db_session, "balance_timing", detected_at=detected_at, state=CaseState.PAUSED.value
    )
    case.paused_until = NOON + dt.timedelta(days=5)  # not due on its own terms yet
    _assign(db_session, case, "treatment", seed)

    result = resolve_scheduled_recoveries(db_session, NOON)

    assert result["recovered"] == 1
    assert result["recovered_while_paused"] == 1


# --- promise breaking ----------------------------------------------------


def test_promise_still_pending_at_grace_deadline_breaks_and_reenters_one_rung_higher(db_session):
    case, _ = _make_treatable_case(db_session, "balance_timing", ladder_rung=2, state=CaseState.PAUSED.value)
    case.paused_until = NOON - dt.timedelta(days=1)
    db_session.commit()

    result = resolve_due_promises(db_session, NOON)

    assert result == {"promises_broken": 1}
    assert case.state == CaseState.IN_TREATMENT.value
    assert case.ladder_rung == 3  # one higher than where it paused — not reset to 0


def test_promise_not_yet_due_is_left_alone(db_session):
    case, _ = _make_treatable_case(db_session, "balance_timing", ladder_rung=1, state=CaseState.PAUSED.value)
    case.paused_until = NOON + dt.timedelta(days=5)
    db_session.commit()

    result = resolve_due_promises(db_session, NOON)

    assert result == {"promises_broken": 0}
    assert case.state == CaseState.PAUSED.value


# --- run_tick: write-off, suppression, holdout exclusion -----------------


def test_ladder_exhaustion_writes_the_case_off(db_session):
    ladder_len = len(load_playbook("subscription").ladders["balance_timing"])
    case, _ = _make_treatable_case(db_session, "balance_timing", ladder_rung=ladder_len)

    result = run_tick(db_session, NOON, 0, CircuitBreaker(), random.Random(1))

    assert case.state == CaseState.WRITTEN_OFF.value
    assert result["written_off"] == 1


def test_dnd_customer_gets_suppressed_not_stuck(db_session):
    case, _ = _make_treatable_case(db_session, "stale_credential", ladder_rung=0, customer_overrides={"dnd": True})

    result = run_tick(db_session, NOON, 0, CircuitBreaker(), random.Random(1))

    assert case.state == CaseState.SUPPRESSED.value
    assert result["suppressed"] == 1


def test_holdout_case_is_never_decided_or_executed(db_session):
    case, _ = _make_treatable_case(db_session, "balance_timing", ladder_rung=0)
    _assign(db_session, case, "holdout", seed=1)
    db_session.commit()

    result = run_tick(db_session, NOON, 0, CircuitBreaker(), random.Random(1))

    assert result["executed"] == 0
    assert result["vetoed"] == 0
    assert case.state == CaseState.IN_TREATMENT.value  # unchanged
    assert case.ladder_rung == 0


def test_dispute_texture_routes_to_human_queue(db_session, monkeypatch):
    case, _ = _make_treatable_case(db_session, "gateway_timeout", ladder_rung=0)
    monkeypatch.setattr("orchestrator.tick.simulate_touch_texture", lambda *a, **k: "dispute")

    result = run_tick(db_session, NOON, 0, CircuitBreaker(), random.Random(1))

    assert case.state == CaseState.HUMAN_QUEUE.value
    assert result["disputed"] == 1
    outcome = db_session.query(Outcome).filter(Outcome.case_id == case.id).one()
    assert outcome.outcome_type == "dispute"


def test_promise_to_pay_texture_pauses_the_case(db_session, monkeypatch):
    case, _ = _make_treatable_case(db_session, "balance_timing", ladder_rung=0)
    monkeypatch.setattr("orchestrator.tick.simulate_touch_texture", lambda *a, **k: "promise_to_pay")

    result = run_tick(db_session, NOON, 0, CircuitBreaker(), random.Random(1))

    assert case.state == CaseState.PAUSED.value
    assert result["paused"] == 1
    assert case.paused_until is not None and case.paused_until > NOON
    outcome = db_session.query(Outcome).filter(Outcome.case_id == case.id).one()
    assert outcome.outcome_type == "ptp"


# --- stuck-rung skip --------------------------------------------------


def test_a_rung_stuck_on_a_case_specific_veto_gets_skipped_after_max_attempts(db_session):
    case, customer = _make_treatable_case(
        db_session, "stale_credential", ladder_rung=1, customer_overrides={"whatsapp_opt_in": False}
    )  # rung 1 = whatsapp_upi_autopay_offer, permanently vetoed for this customer
    circuit_breaker = CircuitBreaker()
    rng = random.Random(1)

    for attempt in range(tick_module.MAX_ATTEMPTS_PER_RUNG):
        totals = dict(executed=0, vetoed=0, routed=0, written_off=0, suppressed=0, skipped_rungs=0, paused=0, disputed=0)
        proposal = decide_case(db_session, case)
        action = execute_proposal(db_session, case, customer, proposal, circuit_breaker, rng, NOON, tick_number=attempt)
        assert action.policy_verdict == "vetoed"
        assert action.veto_reason_code == "WHATSAPP_NOT_OPTED_IN"

        tick_module._handle_veto(db_session, case, action, totals)
        db_session.commit()

        if attempt < tick_module.MAX_ATTEMPTS_PER_RUNG - 1:
            assert totals["skipped_rungs"] == 0
            assert case.ladder_rung == 1
        else:
            assert totals["skipped_rungs"] == 1
            assert case.ladder_rung == 2


def test_global_halt_vetoes_dont_count_toward_the_stuck_rung_threshold(db_session):
    case, customer = _make_treatable_case(db_session, "balance_timing", ladder_rung=0)
    tripped_breaker = CircuitBreaker()
    tripped_breaker.tripped = True
    totals = dict(executed=0, vetoed=0, routed=0, written_off=0, suppressed=0, skipped_rungs=0, paused=0, disputed=0)

    for attempt in range(tick_module.MAX_ATTEMPTS_PER_RUNG + 2):
        proposal = decide_case(db_session, case)
        action = execute_proposal(
            db_session, case, customer, proposal, tripped_breaker, random.Random(1), NOON, tick_number=attempt
        )
        assert action.veto_reason_code == "CIRCUIT_BREAKER_OPEN"
        tick_module._handle_veto(db_session, case, action, totals)
        db_session.commit()

    assert totals["skipped_rungs"] == 0
    assert case.ladder_rung == 0
    assert case.state == CaseState.IN_TREATMENT.value
