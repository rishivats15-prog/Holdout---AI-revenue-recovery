"""Policy gate tests. Every one of the ten checks gets at least one passing
and one failing case, built from directly-constructed contexts rather than
a seeded batch — so each bound is proven independently of the others.
"""

from __future__ import annotations

import datetime as dt

import pytest

from db.models import Action, Case, Customer
from decide.decide import ProposedAction
from policy import checks, reasons
from policy.context import CircuitBreaker, PolicyContext
from policy.gate import evaluate_policy

NOON = dt.datetime(2026, 1, 5, 12, 0)  # naive UTC (Monday, well inside quiet hours) — see db.models.utcnow


def make_customer(**overrides) -> Customer:
    defaults = dict(external_ref="cust_1", phone="9000000000", email="t@example.com", dnd=False, whatsapp_opt_in=True)
    defaults.update(overrides)
    return Customer(**defaults)


def make_case(**overrides) -> Case:
    defaults = dict(
        customer_id=1, lane="subscription", state="in_treatment", exposure_amount=1_000_000,
        detected_at=NOON, ladder_rung=0,
    )
    defaults.update(overrides)
    return Case(**defaults)


def make_proposal(**overrides) -> ProposedAction:
    defaults = dict(
        case_id=1, playbook_id=1, cause="balance_timing", ladder_rung=0,
        action_type="silent_smart_retry", channel="retry", terminal_state=None,
        cooldown_minutes=60, cost_paise=150, copy_template=None, waiver_offer_paise=0, copy_text=None,
    )
    defaults.update(overrides)
    return ProposedAction(**defaults)


def make_action(**overrides) -> Action:
    defaults = dict(
        case_id=1, playbook_id=1, ladder_rung=0, proposed_action="silent_smart_retry",
        channel="retry", policy_verdict="allowed", veto_reason_code=None,
        executed_action="silent_smart_retry", cost=150, idempotency_key="k1", created_at=NOON,
    )
    defaults.update(overrides)
    return Action(**defaults)


def make_ctx(**overrides) -> PolicyContext:
    defaults = dict(
        case=make_case(), customer=make_customer(), proposal=make_proposal(),
        prior_actions=[], now=NOON, circuit_breaker=CircuitBreaker(),
    )
    defaults.update(overrides)
    return PolicyContext(**defaults)


# --- kill switch ---------------------------------------------------------


def test_kill_switch_passes_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(checks, "KILL_SWITCH_PATH", tmp_path / "KILL_SWITCH")
    assert checks.check_kill_switch(make_ctx()) is None


def test_kill_switch_vetoes_when_file_present(tmp_path, monkeypatch):
    switch_path = tmp_path / "KILL_SWITCH"
    switch_path.write_text("halt")
    monkeypatch.setattr(checks, "KILL_SWITCH_PATH", switch_path)
    assert checks.check_kill_switch(make_ctx()) == reasons.KILL_SWITCH_ENGAGED


# --- circuit breaker -------------------------------------------------------


def test_circuit_breaker_passes_when_closed():
    assert checks.check_circuit_breaker(make_ctx(circuit_breaker=CircuitBreaker())) is None


def test_circuit_breaker_vetoes_when_tripped():
    cb = CircuitBreaker()
    cb.tripped = True
    assert checks.check_circuit_breaker(make_ctx(circuit_breaker=cb)) == reasons.CIRCUIT_BREAKER_OPEN


def test_circuit_breaker_trips_after_threshold_failures():
    cb = CircuitBreaker(failure_threshold=0.3, min_sample=10)
    for _ in range(4):
        cb.record_outcome(True)
    for _ in range(4):
        cb.record_outcome(False)
    assert cb.is_open() is False  # only 8 attempts so far, below min_sample
    cb.record_outcome(False)
    cb.record_outcome(False)  # 10 attempts, 6 failures = 60% > 30%
    assert cb.is_open() is True


# --- action allowlist ------------------------------------------------------


def test_action_allowlist_passes_the_real_rung_zero():
    assert checks.check_action_allowlist(make_ctx()) is None


def test_action_allowlist_vetoes_a_mismatched_action_type():
    ctx = make_ctx(proposal=make_proposal(action_type="not_a_real_action"))
    assert checks.check_action_allowlist(ctx) == reasons.ACTION_NOT_IN_PLAYBOOK


def test_action_allowlist_vetoes_rung_past_the_ladder():
    ctx = make_ctx(proposal=make_proposal(ladder_rung=99))
    assert checks.check_action_allowlist(ctx) == reasons.ACTION_NOT_IN_PLAYBOOK


def test_action_allowlist_vetoes_an_inflated_cost():
    ctx = make_ctx(proposal=make_proposal(cost_paise=999_999))
    assert checks.check_action_allowlist(ctx) == reasons.ACTION_NOT_IN_PLAYBOOK


# --- quiet hours -------------------------------------------------------


def test_quiet_hours_passes_during_the_day_for_a_human_channel():
    ctx = make_ctx(proposal=make_proposal(channel="sms"), now=NOON)
    assert checks.check_quiet_hours(ctx) is None


def test_quiet_hours_vetoes_a_late_night_sms():
    late = NOON.replace(hour=23)
    ctx = make_ctx(proposal=make_proposal(channel="sms"), now=late)
    assert checks.check_quiet_hours(ctx) == reasons.OUTSIDE_QUIET_HOURS


def test_quiet_hours_ignores_automated_retry_channel():
    late = NOON.replace(hour=2)
    ctx = make_ctx(proposal=make_proposal(channel="retry"), now=late)
    assert checks.check_quiet_hours(ctx) is None


# --- frequency caps -------------------------------------------------------


def test_frequency_caps_passes_with_no_prior_actions():
    assert checks.check_frequency_caps(make_ctx(proposal=make_proposal(channel="sms"))) is None


def test_frequency_caps_vetoes_second_touch_same_channel_same_day():
    prior = [make_action(channel="sms", created_at=NOON)]
    ctx = make_ctx(proposal=make_proposal(channel="sms", ladder_rung=1), prior_actions=prior, now=NOON)
    assert checks.check_frequency_caps(ctx) == reasons.CHANNEL_FREQUENCY_CAP_EXCEEDED


def test_frequency_caps_vetoes_during_cooldown_window():
    prior = [make_action(channel="retry", ladder_rung=0, created_at=NOON)]  # rung 0 cooldown = 60 min
    soon_after = NOON + dt.timedelta(minutes=10)
    ctx = make_ctx(proposal=make_proposal(channel="sms", ladder_rung=1), prior_actions=prior, now=soon_after)
    assert checks.check_frequency_caps(ctx) == reasons.RUNG_COOLDOWN_ACTIVE


def test_frequency_caps_passes_once_cooldown_elapsed():
    prior = [make_action(channel="retry", ladder_rung=0, created_at=NOON)]
    much_later = NOON + dt.timedelta(minutes=120)
    ctx = make_ctx(proposal=make_proposal(channel="sms", ladder_rung=1), prior_actions=prior, now=much_later)
    assert checks.check_frequency_caps(ctx) is None


# --- consent and eligibility ------------------------------------------------


def test_consent_passes_for_eligible_customer_and_approved_template():
    ctx = make_ctx(
        customer=make_customer(dnd=False, whatsapp_opt_in=True),
        proposal=make_proposal(channel="sms", copy_template="sms_balance_timing_v1"),
    )
    assert checks.check_consent_and_eligibility(ctx) is None


def test_consent_vetoes_dnd_customer_for_human_channel():
    ctx = make_ctx(
        customer=make_customer(dnd=True),
        proposal=make_proposal(channel="sms", copy_template="sms_balance_timing_v1"),
    )
    assert checks.check_consent_and_eligibility(ctx) == reasons.CUSTOMER_DND


def test_consent_ignores_dnd_for_automated_retry():
    ctx = make_ctx(customer=make_customer(dnd=True), proposal=make_proposal(channel="retry"))
    assert checks.check_consent_and_eligibility(ctx) is None


def test_consent_vetoes_whatsapp_without_optin():
    ctx = make_ctx(
        customer=make_customer(whatsapp_opt_in=False),
        proposal=make_proposal(channel="whatsapp", copy_template="whatsapp_alternate_instrument_v1"),
    )
    assert checks.check_consent_and_eligibility(ctx) == reasons.WHATSAPP_NOT_OPTED_IN


def test_consent_vetoes_unregistered_sms_template():
    ctx = make_ctx(proposal=make_proposal(channel="sms", copy_template="not_a_registered_template"))
    assert checks.check_consent_and_eligibility(ctx) == reasons.SMS_TEMPLATE_NOT_DLT_APPROVED


# --- financial authority ----------------------------------------------


def test_financial_authority_passes_under_threshold():
    assert checks.check_financial_authority(make_ctx(proposal=make_proposal(waiver_offer_paise=10_000))) is None


def test_financial_authority_vetoes_over_threshold():
    ctx = make_ctx(proposal=make_proposal(waiver_offer_paise=100_000))
    assert checks.check_financial_authority(ctx) == reasons.FINANCIAL_AUTHORITY_EXCEEDED


# --- cost ceiling -------------------------------------------------------


def test_cost_ceiling_passes_within_budget():
    ctx = make_ctx(case=make_case(exposure_amount=100_000), proposal=make_proposal(cost_paise=1000))
    assert checks.check_cost_ceiling(ctx) is None


def test_cost_ceiling_vetoes_over_budget():
    ctx = make_ctx(case=make_case(exposure_amount=10_000), proposal=make_proposal(cost_paise=5000))
    assert checks.check_cost_ceiling(ctx) == reasons.COST_CEILING_EXCEEDED


def test_cost_ceiling_counts_prior_spend():
    prior = [make_action(cost=700, policy_verdict="allowed")]
    ctx = make_ctx(case=make_case(exposure_amount=10_000), proposal=make_proposal(cost_paise=200), prior_actions=prior)
    assert checks.check_cost_ceiling(ctx) == reasons.COST_CEILING_EXCEEDED


def test_cost_ceiling_ignores_vetoed_prior_spend():
    prior = [make_action(cost=700, policy_verdict="vetoed")]
    ctx = make_ctx(case=make_case(exposure_amount=10_000), proposal=make_proposal(cost_paise=200), prior_actions=prior)
    assert checks.check_cost_ceiling(ctx) is None


# --- expected value floor ------------------------------------------------


def test_ev_floor_passes_when_worth_it():
    ctx = make_ctx(
        case=make_case(exposure_amount=100_000),
        proposal=make_proposal(cause="balance_timing", ladder_rung=0, cost_paise=150),
    )
    assert checks.check_expected_value_floor(ctx) is None


def test_ev_floor_vetoes_when_not_worth_it():
    ctx = make_ctx(
        case=make_case(exposure_amount=100),
        proposal=make_proposal(cause="dead_instrument", ladder_rung=2, cost_paise=300),
    )
    assert checks.check_expected_value_floor(ctx) == reasons.EXPECTED_VALUE_BELOW_COST


# --- content rules -------------------------------------------------------


def test_content_rules_passes_clean_copy():
    ctx = make_ctx(proposal=make_proposal(copy_text="Your payment failed, we'll retry automatically."))
    assert checks.check_content_rules(ctx) is None


def test_content_rules_vetoes_banned_phrase():
    ctx = make_ctx(proposal=make_proposal(copy_text="Pay now or we will sue you."))
    assert checks.check_content_rules(ctx) == reasons.CONTENT_POLICY_VIOLATION


# --- the gate itself -------------------------------------------------------


def test_gate_allows_a_clean_proposal():
    verdict = evaluate_policy(make_ctx())
    assert verdict.allowed is True
    assert verdict.reason_code is None


def test_gate_short_circuits_at_the_first_failing_check(tmp_path, monkeypatch):
    switch_path = tmp_path / "KILL_SWITCH"
    switch_path.write_text("halt")
    monkeypatch.setattr(checks, "KILL_SWITCH_PATH", switch_path)

    # would also fail consent (DND) if the gate kept going — it shouldn't.
    ctx = make_ctx(customer=make_customer(dnd=True), proposal=make_proposal(channel="sms", copy_template="sms_balance_timing_v1"))
    verdict = evaluate_policy(ctx)

    assert verdict.allowed is False
    assert verdict.reason_code == reasons.KILL_SWITCH_ENGAGED
    assert verdict.checks_evaluated == ("kill_switch",)
