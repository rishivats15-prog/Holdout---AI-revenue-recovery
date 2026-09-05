"""eval/report.py — the batch report's arithmetic, checked against
hand-computed answers on hand-built records, plus the database loader that
feeds it.

build_report takes a plain list of CaseRecords, so almost everything here
runs with no session at all. That's deliberate: the measurement layer is
the part of this system a judge has least reason to take on trust, so
every figure it prints has to be reproducible on paper.
"""

from __future__ import annotations

import datetime as dt

import pytest

from db.enums import CaseState, ExperimentArm, PolicyVerdict
from db.models import Action, Case, Customer, Diagnosis, ExperimentAssignment, Outcome
from eval.attribution import ATTRIBUTION_WINDOW_DAYS, days_to_cash, is_within_window
from eval.money import format_paise, format_rate, group_indian
from eval.report import (
    CaseRecord,
    build_report,
    load_case_records,
    load_veto_reason_counts,
    summarize_arm,
)

NOON = dt.datetime(2026, 1, 5, 12, 0)


def _rec(
    case_id: int,
    arm: str,
    *,
    recovered: bool = False,
    days: int | None = None,
    exposure: int = 100_000,
    cause: str = "balance_timing",
    true_cause: str | None = "balance_timing",
    state: str = CaseState.IN_TREATMENT.value,
    touches: int = 0,
    vetoed: int = 0,
    cost: int = 0,
    channels: tuple[str, ...] = (),
    complained: bool = False,
) -> CaseRecord:
    return CaseRecord(
        case_id=case_id,
        arm=arm,
        stratum=f"{cause}:mid",
        diagnosed_cause=cause,
        true_cause=true_cause,
        exposure_paise=exposure,
        state=state,
        recovered_in_window=recovered,
        days_to_cash=days if recovered else None,
        executed_touches=touches,
        vetoed_actions=vetoed,
        cost_paise=cost,
        channels_delivered=channels,
        complained=complained,
    )


# ------------------------------------------------------------ attribution


def test_a_payment_on_the_final_day_of_the_window_counts():
    assert is_within_window(NOON, NOON + dt.timedelta(days=ATTRIBUTION_WINDOW_DAYS))


def test_a_payment_one_day_past_the_window_does_not_count():
    assert not is_within_window(NOON, NOON + dt.timedelta(days=ATTRIBUTION_WINDOW_DAYS, seconds=1))


def test_a_payment_before_detection_is_not_a_recovery_of_this_case():
    assert not is_within_window(NOON, NOON - dt.timedelta(hours=1))


def test_days_to_cash_is_whole_days_since_detection():
    assert days_to_cash(NOON, NOON + dt.timedelta(days=3, hours=5)) == 3


# ------------------------------------------------------------ arm summary


def test_arm_summary_arithmetic_is_hand_checkable():
    records = [
        _rec(1, "treatment", recovered=True, days=4, exposure=100_000, touches=2, cost=300),
        _rec(2, "treatment", recovered=True, days=10, exposure=200_000, touches=3, cost=450, vetoed=1),
        _rec(3, "treatment", exposure=300_000, touches=1, cost=150, vetoed=2),
        _rec(4, "treatment", exposure=400_000, state=CaseState.SUPPRESSED.value, vetoed=1),
    ]

    summary = summarize_arm("treatment", records)

    assert summary.cases == 4
    assert summary.recovered == 2
    assert summary.recovery_rate == 0.5
    assert summary.exposure_at_risk_paise == 1_000_000
    assert summary.mean_exposure_paise == 250_000
    assert summary.gross_recovered_paise == 300_000
    assert summary.executed_touches == 6
    assert summary.vetoed_actions == 4
    assert summary.total_actions == 10
    assert summary.veto_rate == 0.4
    assert summary.cost_paise == 900
    assert summary.median_days_to_cash == 7.0
    assert summary.touches_per_recovery == 3.0
    assert summary.opt_out_rate == 0.25


def test_an_empty_arm_summarizes_to_zeroes_rather_than_dividing_by_zero():
    summary = summarize_arm("holdout", [])
    assert summary.cases == 0
    assert summary.recovery_rate == 0.0
    assert summary.mean_exposure_paise == 0
    assert summary.median_days_to_cash is None
    assert summary.touches_per_recovery is None


def test_holdout_arm_has_no_actions_so_its_veto_rate_is_zero_not_an_error():
    summary = summarize_arm("holdout", [_rec(1, "holdout", recovered=True, days=6)])
    assert summary.total_actions == 0
    assert summary.veto_rate == 0.0


# ----------------------------------------------------------- headline math


def _simple_batch() -> list[CaseRecord]:
    """Treatment: 10 cases, 4 recovered (40%). Holdout: 10 cases, 2
    recovered (20%). Lift is exactly 20pp; mean exposure ₹1,000."""
    treated = [_rec(i, "treatment", recovered=i < 4, days=5, exposure=100_000, touches=1, cost=100) for i in range(10)]
    control = [_rec(100 + i, "holdout", recovered=i < 2, days=9, exposure=100_000) for i in range(10)]
    return treated + control


def test_incremental_rate_is_the_difference_of_the_two_arm_rates():
    report = build_report(_simple_batch())
    assert report.treatment.recovery_rate == pytest.approx(0.4)
    assert report.holdout.recovery_rate == pytest.approx(0.2)
    assert report.incremental_rate == pytest.approx(0.2)


def test_incremental_rupees_is_delta_times_treated_count_times_mean_exposure():
    report = build_report(_simple_batch())
    # 0.20 * 10 cases * 100000 paise = 200000 paise = ₹2,000
    assert report.incremental_paise == 200_000


def test_net_incremental_subtracts_every_paisa_of_channel_cost():
    report = build_report(_simple_batch())
    assert report.total_cost_paise == 1_000  # 10 treated cases x 100 paise
    assert report.net_incremental_paise == 200_000 - 1_000


def test_cost_per_100_is_reported_against_both_incremental_and_gross():
    report = build_report(_simple_batch())
    # 1000 paise spent per 200000 paise incremental -> 50 paise per ₹100
    assert report.cost_per_100_incremental_paise == pytest.approx(50.0)
    # gross recovered is 4 x 100000 = 400000 paise -> 25 paise per ₹100
    assert report.cost_per_100_gross_paise == pytest.approx(25.0)


def test_cost_per_100_is_none_when_nothing_was_recovered():
    """A negative or zero denominator would print as an infinity or a
    nonsense negative — the report shows a dash instead."""
    records = [_rec(i, "treatment", touches=1, cost=100) for i in range(5)]
    records += [_rec(100 + i, "holdout") for i in range(5)]
    report = build_report(records)
    assert report.cost_per_100_incremental_paise is None
    assert report.cost_per_100_gross_paise is None


def test_the_confidence_interval_brackets_the_measured_delta():
    report = build_report(_simple_batch())
    assert report.incremental_ci is not None
    assert report.incremental_ci.lower < report.incremental_rate < report.incremental_ci.upper


def test_no_confidence_interval_when_an_arm_is_empty():
    report = build_report([_rec(i, "treatment") for i in range(5)])
    assert report.incremental_ci is None
    assert report.ground_truth is None


# ------------------------------------------------------- intent-to-treat


def test_suppressed_and_written_off_treatment_cases_stay_in_the_denominator():
    """Dropping them would be marking our own homework: the experiment
    randomized cases to treatment, not to 'treatment we managed to
    deliver'. The holdout comparison is only valid intent-to-treat."""
    treated = [
        _rec(1, "treatment", recovered=True, days=3),
        _rec(2, "treatment", state=CaseState.SUPPRESSED.value, vetoed=1),
        _rec(3, "treatment", state=CaseState.WRITTEN_OFF.value, vetoed=1),
        _rec(4, "treatment", state=CaseState.HUMAN_QUEUE.value),
    ]
    control = [_rec(100, "holdout"), _rec(101, "holdout")]

    report = build_report(treated + control)

    assert report.treatment.cases == 4
    assert report.treatment.recovery_rate == 0.25


def test_unassigned_cases_are_reported_but_never_counted_in_either_arm():
    report = build_report(_simple_batch(), unassigned_cases=7)
    assert report.unassigned_cases == 7
    assert report.total_cases == 20
    assert report.treatment.cases + report.holdout.cases == 20


# --------------------------------------------------------- harm metrics


def test_harm_metrics_are_computed_per_arm():
    treated = [
        _rec(1, "treatment", complained=True, touches=1, vetoed=1),
        _rec(2, "treatment", state=CaseState.SUPPRESSED.value, vetoed=1),
        _rec(3, "treatment", touches=2),
        _rec(4, "treatment", touches=2),
    ]
    control = [_rec(100 + i, "holdout") for i in range(4)]

    report = build_report(treated + control)

    assert report.treatment.complaint_rate == 0.25
    assert report.treatment.opt_out_rate == 0.25
    assert report.treatment.veto_rate == pytest.approx(2 / 7)
    assert report.holdout.complaint_rate == 0.0
    assert report.holdout.opt_out_rate == 0.0


# ------------------------------------------------------------- breakdowns


def test_cause_lift_rows_use_the_diagnosed_cause_and_report_both_arms():
    records = [
        _rec(1, "treatment", cause="mandate_dead", recovered=True, days=2),
        _rec(2, "treatment", cause="mandate_dead"),
        _rec(3, "holdout", cause="mandate_dead"),
        _rec(4, "treatment", cause="balance_timing", recovered=True, days=2),
        _rec(5, "holdout", cause="balance_timing", recovered=True, days=6),
    ]

    report = build_report(records)
    by_cause = {lift.cause: lift for lift in report.cause_lifts}

    assert set(by_cause) == {"mandate_dead", "balance_timing"}
    assert by_cause["mandate_dead"].treatment_rate == 0.5
    assert by_cause["mandate_dead"].holdout_rate == 0.0
    assert by_cause["mandate_dead"].incremental_rate == 0.5
    assert by_cause["balance_timing"].incremental_rate == 0.0


def test_a_cause_present_in_only_one_arm_still_produces_a_row():
    records = [
        _rec(1, "treatment", cause="risk_fraud"),
        _rec(2, "holdout", cause="balance_timing"),
    ]
    report = build_report(records)
    causes = {lift.cause for lift in report.cause_lifts}
    assert causes == {"risk_fraud", "balance_timing"}


def test_channel_rows_overlap_because_a_case_can_be_touched_on_several():
    records = [
        _rec(1, "treatment", recovered=True, days=3, channels=("sms", "whatsapp"), touches=2, cost=200),
        _rec(2, "treatment", channels=("sms",), touches=1, cost=100),
        _rec(3, "holdout"),
        _rec(4, "holdout"),
    ]

    report = build_report(records)
    by_channel = {lift.channel: lift for lift in report.channel_lifts}

    assert by_channel["sms"].cases_touched == 2
    assert by_channel["whatsapp"].cases_touched == 1
    assert by_channel["sms"].recovery_rate == 0.5
    assert by_channel["whatsapp"].recovery_rate == 1.0
    # rows deliberately don't partition the arm
    assert sum(lift.cases_touched for lift in report.channel_lifts) > report.treatment.cases


def test_channel_delta_is_measured_against_the_holdout_rate():
    records = [
        _rec(1, "treatment", recovered=True, days=3, channels=("sms",), touches=1),
        _rec(2, "holdout", recovered=True, days=3),
        _rec(3, "holdout"),
    ]
    report = build_report(records)
    assert report.holdout.recovery_rate == 0.5
    assert report.channel_lifts[0].delta_vs_holdout == pytest.approx(0.5)


def test_diagnosis_accuracy_compares_diagnosed_against_true_cause():
    records = [
        _rec(1, "treatment", cause="balance_timing", true_cause="balance_timing"),
        _rec(2, "treatment", cause="balance_timing", true_cause="stale_credential"),
        _rec(3, "holdout", cause="mandate_dead", true_cause="mandate_dead"),
        _rec(4, "holdout", cause="mandate_dead", true_cause="mandate_dead"),
    ]
    report = build_report(records)
    assert report.diagnosis_accuracy.correct == 3
    assert report.diagnosis_accuracy.accuracy == 0.75


def test_diagnosis_accuracy_is_none_without_any_ground_truth():
    records = [_rec(i, "treatment", true_cause=None) for i in range(3)]
    report = build_report(records)
    assert report.diagnosis_accuracy is None


# ---------------------------------------------------------------- loader


def _seed_case(db_session, *, arm: str, cause: str, exposure: int = 100_000, state: str = CaseState.IN_TREATMENT.value):
    customer = Customer(external_ref=f"cust_rep_{_next_ref()}")
    db_session.add(customer)
    db_session.flush()
    case = Case(
        customer_id=customer.id, lane="subscription", state=state,
        exposure_amount=exposure, detected_at=NOON, true_root_cause=cause,
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(Diagnosis(case_id=case.id, cause=cause, confidence=1.0, method="rule", evidence={}))
    db_session.add(ExperimentAssignment(case_id=case.id, arm=arm, stratum=f"{cause}:mid", seed=1))
    db_session.flush()
    return case


_ref_counter = 0


def _next_ref() -> int:
    global _ref_counter
    _ref_counter += 1
    return _ref_counter


def _add_action(db_session, case, *, verdict: str, channel: str, cost: int = 100, delivered: bool | None = True, reason=None):
    action = Action(
        case_id=case.id, ladder_rung=0, proposed_action=f"{channel}_touch", channel=channel,
        policy_verdict=verdict, veto_reason_code=reason, executed_action=channel if verdict == "allowed" else None,
        cost=cost if verdict == "allowed" else 0, delivered=delivered if verdict == "allowed" else None,
        idempotency_key=f"key_{_next_ref()}",
    )
    db_session.add(action)
    db_session.flush()
    return action


def test_loader_flattens_a_case_with_its_actions_and_outcomes(db_session):
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    _add_action(db_session, case, verdict=PolicyVerdict.ALLOWED.value, channel="sms", cost=120)
    _add_action(db_session, case, verdict=PolicyVerdict.VETOED.value, channel="whatsapp", reason="WHATSAPP_NOT_OPTED_IN")
    db_session.add(Outcome(case_id=case.id, outcome_type="payment", amount=100_000, occurred_at=NOON + dt.timedelta(days=6)))
    db_session.commit()

    records, unassigned = load_case_records(db_session)

    assert unassigned == 0
    record = records[0]
    assert record.recovered_in_window is True
    assert record.days_to_cash == 6
    assert record.executed_touches == 1
    assert record.vetoed_actions == 1
    assert record.cost_paise == 120  # a vetoed action costs nothing — nothing was sent
    assert record.channels_delivered == ("sms",)


def test_loader_excludes_a_payment_outside_the_attribution_window(db_session):
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing", state=CaseState.RECOVERED.value)
    db_session.add(Outcome(case_id=case.id, outcome_type="payment", amount=100_000, occurred_at=NOON + dt.timedelta(days=18)))
    db_session.commit()

    records, _ = load_case_records(db_session)

    assert records[0].state == CaseState.RECOVERED.value
    assert records[0].recovered_in_window is False
    assert records[0].days_to_cash is None


def test_loader_takes_the_earliest_in_window_payment_for_days_to_cash(db_session):
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    db_session.add(Outcome(case_id=case.id, outcome_type="payment", amount=50_000, occurred_at=NOON + dt.timedelta(days=9)))
    db_session.add(Outcome(case_id=case.id, outcome_type="payment", amount=50_000, occurred_at=NOON + dt.timedelta(days=4)))
    db_session.commit()

    records, _ = load_case_records(db_session)

    assert records[0].days_to_cash == 4


def test_loader_marks_a_dispute_as_a_complaint(db_session):
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    db_session.add(Outcome(case_id=case.id, outcome_type="dispute", occurred_at=NOON))
    db_session.commit()

    records, _ = load_case_records(db_session)

    assert records[0].complained is True


def test_loader_skips_cases_that_were_never_randomized(db_session):
    _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    customer = Customer(external_ref=f"cust_rep_{_next_ref()}")
    db_session.add(customer)
    db_session.flush()
    orphan = Case(
        customer_id=customer.id, lane="subscription", state=CaseState.DETECTED.value,
        exposure_amount=50_000, detected_at=NOON, true_root_cause="balance_timing",
    )
    db_session.add(orphan)
    db_session.commit()

    records, unassigned = load_case_records(db_session)

    assert len(records) == 1
    assert unassigned == 1


def test_loader_uses_the_latest_diagnosis_when_a_case_was_rediagnosed(db_session):
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    db_session.add(Diagnosis(case_id=case.id, cause="stale_credential", confidence=0.8, method="llm_fallback", evidence={}))
    db_session.commit()

    records, _ = load_case_records(db_session)

    assert records[0].diagnosed_cause == "stale_credential"


def test_loader_records_only_delivered_channels(db_session):
    """An allowed-but-undelivered send didn't reach anyone, so it can't be
    credited with a recovery in the channel table."""
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    _add_action(db_session, case, verdict=PolicyVerdict.ALLOWED.value, channel="sms", delivered=False)
    _add_action(db_session, case, verdict=PolicyVerdict.ALLOWED.value, channel="voice", delivered=True)
    db_session.commit()

    records, _ = load_case_records(db_session)

    assert records[0].channels_delivered == ("voice",)
    assert records[0].executed_touches == 2


def test_veto_reason_counts_are_ordered_by_frequency(db_session):
    case = _seed_case(db_session, arm=ExperimentArm.TREATMENT.value, cause="balance_timing")
    for _ in range(3):
        _add_action(db_session, case, verdict=PolicyVerdict.VETOED.value, channel="whatsapp", reason="WHATSAPP_NOT_OPTED_IN")
    _add_action(db_session, case, verdict=PolicyVerdict.VETOED.value, channel="sms", reason="CUSTOMER_DND")
    db_session.commit()

    counts = load_veto_reason_counts(db_session)

    assert counts == (("WHATSAPP_NOT_OPTED_IN", 3), ("CUSTOMER_DND", 1))


# ----------------------------------------------------------------- money


def test_indian_digit_grouping():
    assert group_indian("1") == "1"
    assert group_indian("999") == "999"
    assert group_indian("1234") == "1,234"
    assert group_indian("1245890") == "12,45,890"
    assert group_indian("100000000") == "10,00,00,000"


def test_format_paise_rounds_to_rupees_for_display_only():
    assert format_paise(1_245_890_00) == "₹12,45,890"
    assert format_paise(-50_000) == "-₹500"
    assert format_paise(155_910, paise_precision=True) == "₹1,559.10"


def test_format_rate_renders_none_as_a_dash():
    assert format_rate(None) == "—"
    assert format_rate(0.0900) == "9.00%"
