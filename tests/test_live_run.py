"""The live run: orchestrator/session.py and the dashboard's /run page.

The test that matters most is
`test_stepping_day_by_day_lands_where_the_batch_runner_lands`. The live
page is only trustworthy as a demo if it is genuinely the same pipeline —
if it were a simplified copy, watching it would tell a judge nothing about
what `make seed` actually does.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dashboard.viz import build_run_progression
from db.models import Action, Base, Case, CaseEvent, Outcome
from diagnosis.diagnose import diagnose_all_detected
from eval.assignment import assign_experiment_arms
from orchestrator.runner import run_orchestrator
from orchestrator.session import RunSession, TickResult
from sim.generator import generate_batch

FIXED_NOW = dt.datetime(2026, 3, 1, 9, 30)
START = FIXED_NOW.replace(hour=12, minute=0, second=0, microsecond=0)
BATCH_SIZE = 120


def _fresh_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _seed_without_running(db) -> None:
    generate_batch(db, n_customers=BATCH_SIZE, now=FIXED_NOW)
    diagnose_all_detected(db)
    assign_experiment_arms(db)


def _fingerprint(db) -> dict:
    """Everything a run decides, reduced to something comparable."""
    return {
        "states": sorted((case.id, case.state, case.ladder_rung) for case in db.query(Case).all()),
        "actions": sorted(
            (a.case_id, a.ladder_rung, a.policy_verdict, a.veto_reason_code, a.cost, a.delivered)
            for a in db.query(Action).all()
        ),
        "outcomes": sorted(
            (o.case_id, o.outcome_type, o.amount, o.occurred_at) for o in db.query(Outcome).all()
        ),
        "event_types": sorted((e.case_id, e.event_type) for e in db.query(CaseEvent).all()),
    }


def test_stepping_day_by_day_lands_where_the_batch_runner_lands():
    """`make seed` runs 21 days in one pass; the dashboard runs them one
    click at a time. Both drive RunSession.advance with the same seeds, so
    the resulting databases must be indistinguishable — same case states,
    same actions with the same verdicts, same outcomes, same ledger."""
    batch_db, batch_engine = _fresh_db()
    stepped_db, stepped_engine = _fresh_db()
    try:
        _seed_without_running(batch_db)
        run_orchestrator(batch_db, n_ticks=21, start=START)

        _seed_without_running(stepped_db)
        session = RunSession(start=START, total_ticks=21)
        while not session.is_complete:
            session.advance(stepped_db)

        assert _fingerprint(stepped_db) == _fingerprint(batch_db)
    finally:
        batch_db.close(); batch_engine.dispose()
        stepped_db.close(); stepped_engine.dispose()


def test_the_session_totals_match_the_batch_runners_totals():
    db, engine = _fresh_db()
    try:
        _seed_without_running(db)
        session = RunSession(start=START, total_ticks=21)
        while not session.is_complete:
            session.advance(db)
        totals = session.run_totals()
    finally:
        db.close(); engine.dispose()

    batch_db, batch_engine = _fresh_db()
    try:
        _seed_without_running(batch_db)
        assert run_orchestrator(batch_db, n_ticks=21, start=START) == totals
    finally:
        batch_db.close(); batch_engine.dispose()


def test_a_tick_result_accumulates_into_the_running_totals():
    db, engine = _fresh_db()
    try:
        _seed_without_running(db)
        session = RunSession(start=START, total_ticks=21)
        first = session.advance(db)
        second = session.advance(db)

        assert session.tick_number == 2
        assert session.history == [first, second]
        assert session.totals["executed"] == first.executed + second.executed
        assert session.totals["vetoed"] == first.vetoed + second.vetoed
        assert session.totals["recovered"] == first.recovered + second.recovered
    finally:
        db.close(); engine.dispose()


def test_advance_days_never_ticks_past_the_end_of_the_run():
    db, engine = _fresh_db()
    try:
        _seed_without_running(db)
        session = RunSession(start=START, total_ticks=5)
        results = session.advance_days(db, 50)

        assert len(results) == 5
        assert session.tick_number == 5
        assert session.is_complete
        assert session.advance_days(db, 3) == []
    finally:
        db.close(); engine.dispose()


def test_progress_reporting():
    session = RunSession(total_ticks=10)
    assert session.progress_pct == 0.0
    assert session.days_remaining == 10
    session.tick_number = 4
    assert session.progress_pct == 40.0
    assert session.days_remaining == 6
    assert not session.is_complete


def test_each_tick_records_the_simulated_day_it_ran_for():
    db, engine = _fresh_db()
    try:
        _seed_without_running(db)
        session = RunSession(start=START, total_ticks=3)
        results = session.advance_days(db, 3)
        assert [r.day for r in results] == [START, START + dt.timedelta(days=1), START + dt.timedelta(days=2)]
        assert [r.day_label for r in results] == ["Day 1", "Day 2", "Day 3"]
    finally:
        db.close(); engine.dispose()


# --- the progression charts ---------------------------------------------


def _tick(i, recovered=0, executed=0, vetoed=0):
    return TickResult(
        tick_number=i, day=START + dt.timedelta(days=i), executed=executed, vetoed=vetoed,
        routed=0, written_off=0, suppressed=0, skipped_rungs=0, paused=0, disputed=0,
        recovered=recovered, recovered_while_paused=0, promises_broken=0,
    )


def test_no_chart_before_the_first_day_has_run():
    assert build_run_progression([], total_ticks=21) is None


def test_the_charts_plot_cumulative_values():
    history = [_tick(0, recovered=5, executed=10, vetoed=2), _tick(1, recovered=3, executed=8, vetoed=4)]
    progression = build_run_progression(history, total_ticks=21, total_cases=100)

    assert progression.recoveries.series[0].final_value == 8
    by_label = {s.label: s.final_value for s in progression.actions.series}
    assert by_label == {"Touches sent": 18, "Vetoed": 6}


def test_recoveries_are_scaled_against_the_whole_batch_not_their_own_maximum():
    """Otherwise every run's recovery line would reach the top of the
    chart, and a poor run would look identical to a good one."""
    history = [_tick(0, recovered=5)]
    progression = build_run_progression(history, total_ticks=21, total_cases=900)
    assert progression.recoveries.max_value == 900


def test_sent_and_vetoed_share_one_scale_because_they_are_the_same_unit():
    history = [_tick(0, executed=100, vetoed=25)]
    progression = build_run_progression(history, total_ticks=21, total_cases=900)
    assert progression.actions.max_value == 100
    sent, vetoed = progression.actions.series
    assert vetoed.final_y > sent.final_y  # lower value sits lower on the chart


def test_the_x_axis_spans_the_whole_run_so_lines_advance_across_it():
    """Scaling x to the days elapsed would make a two-day run and a
    twenty-day run look the same width, and the lines would never appear
    to progress."""
    early = build_run_progression([_tick(0, recovered=1)], total_ticks=21, total_cases=900)
    later = build_run_progression([_tick(i, recovered=1) for i in range(10)], total_ticks=21, total_cases=900)
    assert later.recoveries.series[0].final_x > early.recoveries.series[0].final_x


def test_day_labels_are_not_clipped_at_the_left_edge():
    progression = build_run_progression([_tick(0)], total_ticks=21, total_cases=900)
    first_x, first_label = progression.recoveries.day_ticks[0]
    assert first_label == "D1"
    assert first_x > 6  # centred text needs room to its left
