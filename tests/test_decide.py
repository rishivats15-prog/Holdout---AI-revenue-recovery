"""Decide step tests. The core invariant: decide_case never branches on a
cause by name — it only ever looks up "what's the next rung for this
cause?" in the playbook YAML and acts on whatever that says.
"""

from __future__ import annotations

import pytest

from db.enums import CaseState
from db.models import Case, Customer, Diagnosis, utcnow
from decide.decide import decide_case
from decide.ladder import load_playbook, next_rung
from diagnosis.rules import load_rule_table

SUBSCRIPTION_CAUSES = sorted(load_rule_table("subscription").causes)


def test_playbook_causes_match_diagnosis_causes():
    playbook = load_playbook("subscription")
    assert set(playbook.ladders) == load_rule_table("subscription").causes


def test_every_rung_has_exactly_one_of_channel_or_terminal_state():
    playbook = load_playbook("subscription")
    for cause, ladder in playbook.ladders.items():
        for rung in ladder:
            has_channel = rung.channel is not None
            has_terminal = rung.terminal_state is not None
            assert has_channel != has_terminal, f"{cause} rung {rung.rung} must set exactly one"


def test_rungs_are_sequential_from_zero():
    playbook = load_playbook("subscription")
    for cause, ladder in playbook.ladders.items():
        assert [r.rung for r in ladder] == list(range(len(ladder))), cause


def test_next_rung_returns_none_past_the_ladder():
    playbook = load_playbook("subscription")
    for cause, ladder in playbook.ladders.items():
        assert next_rung("subscription", cause, len(ladder)) is None


def _make_case(db_session, cause: str, ladder_rung: int = 0, state: str = CaseState.DIAGNOSED.value) -> Case:
    customer = Customer(external_ref=f"cust_{cause}_{ladder_rung}_{state}")
    db_session.add(customer)
    db_session.flush()

    case = Case(
        customer_id=customer.id,
        lane="subscription",
        state=state,
        exposure_amount=10000,
        detected_at=utcnow(),
        ladder_rung=ladder_rung,
        true_root_cause=cause,
    )
    db_session.add(case)
    db_session.flush()

    diagnosis = Diagnosis(case_id=case.id, cause=cause, confidence=1.0, method="rule", evidence={})
    db_session.add(diagnosis)
    db_session.flush()
    return case


@pytest.mark.parametrize("cause", SUBSCRIPTION_CAUSES)
def test_decide_case_proposes_exactly_the_playbooks_rung_zero(db_session, cause):
    case = _make_case(db_session, cause)
    proposal = decide_case(db_session, case)
    expected_rung = next_rung("subscription", cause, 0)

    assert proposal is not None
    assert proposal.action_type == expected_rung.action_type
    assert proposal.channel == expected_rung.channel
    assert proposal.terminal_state == expected_rung.terminal_state
    assert proposal.cost_paise == expected_rung.cost_paise


def test_channel_rung_moves_case_from_diagnosed_to_in_treatment(db_session):
    case = _make_case(db_session, "balance_timing")
    decide_case(db_session, case)
    assert case.state == CaseState.IN_TREATMENT.value


def test_terminal_rung_routes_straight_to_its_declared_state(db_session):
    case = _make_case(db_session, "risk_fraud")
    decide_case(db_session, case)
    assert case.state == CaseState.HUMAN_QUEUE.value


def test_exhausted_ladder_returns_none_without_changing_state(db_session):
    ladder_len = len(load_playbook("subscription").ladders["balance_timing"])
    case = _make_case(
        db_session, "balance_timing", ladder_rung=ladder_len, state=CaseState.IN_TREATMENT.value
    )
    proposal = decide_case(db_session, case)
    assert proposal is None
    assert case.state == CaseState.IN_TREATMENT.value
