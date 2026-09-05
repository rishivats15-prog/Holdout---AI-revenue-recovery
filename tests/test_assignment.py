"""eval/assignment.py — the randomization the whole measurement rests on.

The properties that matter: every eligible case gets exactly one arm, the
split is balanced *within* each stratum (not just overall), assignment is
reproducible from the seed, and re-running never re-rolls a case that was
already assigned.
"""

from __future__ import annotations

import datetime as dt

import pytest

from db.enums import CaseState, ExperimentArm
from db.models import Case, Customer, Diagnosis, ExperimentAssignment
from eval.assignment import (
    EXPOSURE_BANDS,
    assign_experiment_arms,
    case_seed,
    exposure_band,
    stratum_key,
)

NOON = dt.datetime(2026, 1, 5, 12, 0)


def _make_case(db_session, cause: str, exposure_paise: int, state: str = CaseState.DIAGNOSED.value) -> Case:
    customer = Customer(external_ref=f"cust_asg_{_next_ref()}")
    db_session.add(customer)
    db_session.flush()
    case = Case(
        customer_id=customer.id, lane="subscription", state=state,
        exposure_amount=exposure_paise, detected_at=NOON, true_root_cause=cause,
    )
    db_session.add(case)
    db_session.flush()
    db_session.add(Diagnosis(case_id=case.id, cause=cause, confidence=1.0, method="rule", evidence={}))
    db_session.flush()
    return case


_ref_counter = 0


def _next_ref() -> int:
    global _ref_counter
    _ref_counter += 1
    return _ref_counter


def _arms(db_session) -> dict[int, str]:
    return {row.case_id: row.arm for row in db_session.query(ExperimentAssignment).all()}


def test_exposure_bands_cover_the_whole_range_without_gaps():
    assert exposure_band(0) == "low"
    assert exposure_band(29_999) == "low"
    assert exposure_band(30_000) == "mid"
    assert exposure_band(119_999) == "mid"
    assert exposure_band(120_000) == "high"
    assert exposure_band(10_000_000) == "high"


def test_bands_are_contiguous_as_declared():
    """Each band starts exactly where the previous one ends — a gap would
    make exposure_band raise on a real case."""
    for (_, _, upper), (_, lower, _) in zip(EXPOSURE_BANDS, EXPOSURE_BANDS[1:]):
        assert upper == lower


def test_stratum_key_combines_cause_and_band():
    assert stratum_key("balance_timing", 50_000) == "balance_timing:mid"


def test_every_diagnosed_case_gets_exactly_one_assignment(db_session):
    for i in range(40):
        _make_case(db_session, "balance_timing", 50_000 + i)

    stats = assign_experiment_arms(db_session)

    assert stats["cases_assigned"] == 40
    assert stats["treatment"] + stats["holdout"] == 40
    assert len(_arms(db_session)) == 40


def test_holdout_share_is_applied_within_each_stratum_not_just_overall(db_session):
    """Stratification is the whole point: an overall 15% split that put
    every mandate_dead case in one arm would leave the arms incomparable
    on exactly the variable that drives recovery."""
    for cause in ("balance_timing", "mandate_dead"):
        for _ in range(20):
            _make_case(db_session, cause, 50_000)  # all mid band

    assign_experiment_arms(db_session, holdout_fraction=0.25)

    rows = db_session.query(ExperimentAssignment).all()
    per_stratum: dict[str, list[str]] = {}
    for row in rows:
        per_stratum.setdefault(row.stratum, []).append(row.arm)

    assert set(per_stratum) == {"balance_timing:mid", "mandate_dead:mid"}
    for arms in per_stratum.values():
        assert arms.count(ExperimentArm.HOLDOUT.value) == 5
        assert arms.count(ExperimentArm.TREATMENT.value) == 15


def test_cases_are_stratified_by_exposure_band_as_well_as_cause(db_session):
    for exposure in (10_000, 50_000, 500_000):
        for _ in range(8):
            _make_case(db_session, "stale_credential", exposure)

    stats = assign_experiment_arms(db_session)

    strata = {row.stratum for row in db_session.query(ExperimentAssignment).all()}
    assert strata == {"stale_credential:low", "stale_credential:mid", "stale_credential:high"}
    assert stats["strata"] == 3


def test_assignment_is_idempotent_and_never_re_rolls_an_assigned_case(db_session):
    for _ in range(30):
        _make_case(db_session, "balance_timing", 50_000)
    assign_experiment_arms(db_session)
    first_pass = _arms(db_session)

    stats = assign_experiment_arms(db_session)

    assert stats["cases_assigned"] == 0
    assert _arms(db_session) == first_pass


def test_a_later_batch_of_cases_is_assigned_without_disturbing_the_first(db_session):
    for _ in range(20):
        _make_case(db_session, "balance_timing", 50_000)
    assign_experiment_arms(db_session)
    first_pass = _arms(db_session)

    for _ in range(20):
        _make_case(db_session, "gateway_timeout", 50_000)
    stats = assign_experiment_arms(db_session)

    assert stats["cases_assigned"] == 20
    after = _arms(db_session)
    assert {case_id: after[case_id] for case_id in first_pass} == first_pass
    assert len(after) == 40


def test_only_diagnosed_cases_are_assigned(db_session):
    _make_case(db_session, "balance_timing", 50_000, state=CaseState.DETECTED.value)
    _make_case(db_session, "balance_timing", 50_000, state=CaseState.DIAGNOSED.value)

    stats = assign_experiment_arms(db_session)

    assert stats["cases_assigned"] == 1


def test_the_stored_seed_is_deterministic_per_case_and_distinct_between_cases():
    assert case_seed(17) == case_seed(17)
    assert case_seed(17) != case_seed(18)


def test_stored_seed_matches_the_recorded_assignment_row(db_session):
    case = _make_case(db_session, "balance_timing", 50_000)
    assign_experiment_arms(db_session)
    row = db_session.query(ExperimentAssignment).filter_by(case_id=case.id).one()
    assert row.seed == case_seed(case.id)


def test_a_zero_holdout_fraction_puts_everything_in_treatment(db_session):
    for _ in range(10):
        _make_case(db_session, "balance_timing", 50_000)

    stats = assign_experiment_arms(db_session, holdout_fraction=0.0)

    assert stats["holdout"] == 0
    assert stats["treatment"] == 10


def test_exposure_band_rejects_a_negative_exposure():
    with pytest.raises(ValueError):
        exposure_band(-1)
