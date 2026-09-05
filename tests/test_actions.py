"""Execute step tests: the allowed path, the vetoed path, idempotency
actually preventing a double-send, and the circuit breaker halting a batch
once it's open.
"""

from __future__ import annotations

import datetime as dt
import random

from actions.execute import build_idempotency_key, execute_proposal, run_batch
from db.models import Action, Case, Customer
from decide.decide import ProposedAction
from policy import reasons
from policy.context import CircuitBreaker

NOON = dt.datetime(2026, 1, 5, 12, 0)  # naive UTC, matching db.models.utcnow


_customer_counter = 0


def _seed_case(db_session, **overrides) -> tuple[Case, Customer]:
    global _customer_counter
    _customer_counter += 1
    customer = Customer(external_ref=f"cust_x_{_customer_counter}", phone="9000000000", dnd=False, whatsapp_opt_in=True)
    db_session.add(customer)
    db_session.flush()

    defaults = dict(
        customer_id=customer.id, lane="subscription", state="in_treatment",
        exposure_amount=1_000_000, detected_at=NOON, ladder_rung=0,
    )
    defaults.update(overrides)
    case = Case(**defaults)
    db_session.add(case)
    db_session.flush()
    return case, customer


def _proposal(case: Case, **overrides) -> ProposedAction:
    defaults = dict(
        case_id=case.id, playbook_id=1, cause="balance_timing", ladder_rung=0,
        action_type="silent_smart_retry", channel="retry", terminal_state=None,
        cooldown_minutes=60, cost_paise=150, copy_template=None, waiver_offer_paise=0,
    )
    defaults.update(overrides)
    return ProposedAction(**defaults)


def test_allowed_action_writes_row_and_advances_the_rung(db_session):
    case, customer = _seed_case(db_session)
    proposal = _proposal(case)

    action = execute_proposal(db_session, case, customer, proposal, CircuitBreaker(), random.Random(1), NOON)

    assert action.policy_verdict == "allowed"
    assert action.executed_action == "silent_smart_retry"
    assert action.cost == 150
    assert action.external_id is not None
    assert case.ladder_rung == 1


def test_vetoed_action_writes_row_with_reason_and_no_cost(db_session):
    # exposure too small for even rung 0's real cost to clear the ceiling
    case, customer = _seed_case(db_session, exposure_amount=1000)
    proposal = _proposal(case)

    action = execute_proposal(db_session, case, customer, proposal, CircuitBreaker(), random.Random(1), NOON)

    assert action.policy_verdict == "vetoed"
    assert action.veto_reason_code == reasons.COST_CEILING_EXCEEDED
    assert action.cost == 0
    assert action.executed_action is None
    assert case.ladder_rung == 0  # a vetoed attempt never consumes the rung


def test_idempotency_key_reused_returns_existing_row_without_resending(db_session):
    case, customer = _seed_case(db_session)
    proposal = _proposal(case)
    rng = random.Random(1)

    first = execute_proposal(db_session, case, customer, proposal, CircuitBreaker(), rng, NOON)
    count_after_first = db_session.query(Action).count()

    second = execute_proposal(db_session, case, customer, proposal, CircuitBreaker(), rng, NOON)
    count_after_second = db_session.query(Action).count()

    assert first.id == second.id
    assert count_after_first == count_after_second
    assert first.idempotency_key == build_idempotency_key(case.id, proposal.playbook_id, proposal.ladder_rung)


def test_circuit_breaker_open_vetoes_regardless_of_other_checks(db_session):
    cb = CircuitBreaker(failure_threshold=0.2, min_sample=5)
    for _ in range(5):
        cb.record_outcome(False)
    assert cb.is_open() is True

    case, customer = _seed_case(db_session)  # otherwise a perfectly clean proposal
    proposal = _proposal(case)

    action = execute_proposal(db_session, case, customer, proposal, cb, random.Random(1), NOON)

    assert action.policy_verdict == "vetoed"
    assert action.veto_reason_code == reasons.CIRCUIT_BREAKER_OPEN


def test_run_batch_skips_channel_less_proposals_without_crashing(db_session):
    case, _ = _seed_case(db_session)
    routed_proposal = _proposal(case, action_type="route_to_risk_queue", channel=None, terminal_state="human_queue")
    real_proposal = _proposal(case)

    summary = run_batch(db_session, [routed_proposal, real_proposal], now=NOON)

    assert summary["routed"] == 1
    assert summary["allowed"] + summary["vetoed"] == 1


def test_run_batch_produces_a_mix_and_reports_counts(db_session):
    case1, _ = _seed_case(db_session, exposure_amount=1_000_000)
    case2, _ = _seed_case(db_session, exposure_amount=1000)
    proposals = [_proposal(case1), _proposal(case2)]

    summary = run_batch(db_session, proposals, now=NOON)

    assert summary["allowed"] + summary["vetoed"] == 2
    assert summary["allowed"] >= 1
    assert summary["vetoed"] >= 1
    assert db_session.query(Action).filter(Action.policy_verdict == "vetoed").count() >= 1
    assert db_session.query(Action).filter(Action.policy_verdict == "allowed").count() >= 1
