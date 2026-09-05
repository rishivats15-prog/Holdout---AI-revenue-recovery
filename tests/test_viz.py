"""dashboard/viz.py — the flow diagram's geometry.

The scoreboard's hero claims that band thickness is proportional to
rupees. These tests are what make that claim checkable: every band sums to
its parent, the scale is uniform across the whole diagram, and the
incremental callout is anchored to the band it measures rather than
positioned by eye.
"""

from __future__ import annotations

import datetime as dt

import pytest

from dashboard.viz import (
    ARM_GAP,
    FLOW_HEIGHT,
    OUTCOME_GAP,
    build_flow_diagram,
    build_harm_bars,
    build_interval_plot,
    build_veto_distribution,
)
from db.enums import CaseState
from eval.report import CaseRecord, build_report


def _records(treated_n=80, treated_recovered=32, holdout_n=20, holdout_recovered=5, exposure=100_000):
    treated = [
        CaseRecord(
            case_id=i, arm="treatment", stratum="balance_timing:mid", diagnosed_cause="balance_timing",
            true_cause="balance_timing", exposure_paise=exposure, state=CaseState.IN_TREATMENT.value,
            recovered_in_window=i < treated_recovered, days_to_cash=4 if i < treated_recovered else None,
            executed_touches=2, vetoed_actions=1, cost_paise=120, channels_delivered=("sms",),
        )
        for i in range(treated_n)
    ]
    holdout = [
        CaseRecord(
            case_id=1000 + i, arm="holdout", stratum="balance_timing:mid", diagnosed_cause="balance_timing",
            true_cause="balance_timing", exposure_paise=exposure, state=CaseState.DIAGNOSED.value,
            recovered_in_window=i < holdout_recovered, days_to_cash=7 if i < holdout_recovered else None,
            executed_touches=0, vetoed_actions=0, cost_paise=0,
        )
        for i in range(holdout_n)
    ]
    return treated + holdout


def _diagram(**kwargs):
    return build_flow_diagram(build_report(_records(**kwargs)))


def _band(diagram, key):
    return next(band for band in diagram.bands if band.key == key)


def test_the_arm_bands_sum_to_the_exposure_band():
    diagram = _diagram()
    total = _band(diagram, "treated").height + _band(diagram, "holdout").height
    assert total == pytest.approx(_band(diagram, "exposure").height, abs=0.01)


def test_each_arms_outcome_bands_sum_to_that_arm():
    diagram = _diagram()
    treated = _band(diagram, "t_lost").height + _band(diagram, "t_baseline").height + _band(diagram, "t_incremental").height
    assert treated == pytest.approx(_band(diagram, "treated").height, abs=0.01)

    holdout = _band(diagram, "h_recovered").height + _band(diagram, "h_lost").height
    assert holdout == pytest.approx(_band(diagram, "holdout").height, abs=0.01)


def test_one_uniform_scale_governs_every_band():
    """A per-column scale would make bands look comparable when they are
    not — the whole diagram has to share one rupees-per-pixel."""
    diagram = _diagram()
    report = build_report(_records())
    scale = _band(diagram, "treated").height / report.treatment.exposure_at_risk_paise
    for key, paise in (
        ("holdout", report.holdout.exposure_at_risk_paise),
        ("h_recovered", report.holdout.gross_recovered_paise),
        ("t_lost", report.treatment.exposure_at_risk_paise - report.treatment.gross_recovered_paise),
    ):
        assert _band(diagram, key).height == pytest.approx(paise * scale, abs=0.01)


def test_the_diagram_fits_its_canvas_including_the_gaps():
    diagram = _diagram()
    bottom = max(band.y + band.height for band in diagram.bands)
    assert bottom <= diagram.height
    outcome_span = _band(diagram, "h_lost").y + _band(diagram, "h_lost").height - _band(diagram, "t_lost").y
    assert outcome_span == pytest.approx(FLOW_HEIGHT - ARM_GAP, abs=0.01)


def test_the_two_recovered_bands_are_adjacent():
    """The incremental slice sits directly above the holdout's recovered
    band, separated only by the standard gap — that adjacency is what
    lets the eye read the difference."""
    diagram = _diagram()
    incremental = _band(diagram, "t_incremental")
    holdout_recovered = _band(diagram, "h_recovered")
    gap = holdout_recovered.y - (incremental.y + incremental.height)
    assert gap == pytest.approx(OUTCOME_GAP, abs=0.01)


def test_the_recovered_slices_are_a_single_band_with_no_seam():
    diagram = _diagram()
    baseline = _band(diagram, "t_baseline")
    incremental = _band(diagram, "t_incremental")
    assert incremental.y == pytest.approx(baseline.y + baseline.height, abs=0.01)


def test_the_incremental_slice_carries_the_reported_incremental_figure():
    report = build_report(_records())
    diagram = build_flow_diagram(report)
    scale = _band(diagram, "treated").height / report.treatment.exposure_at_risk_paise
    assert _band(diagram, "t_incremental").height == pytest.approx(report.incremental_paise * scale, abs=0.01)
    assert diagram.callout.label == diagram.incremental_label


def test_the_callout_bracket_spans_exactly_the_incremental_band():
    diagram = _diagram()
    band = _band(diagram, "t_incremental")
    assert f"{band.y:.2f}" in diagram.callout.bracket_path
    assert f"{band.y + band.height:.2f}" in diagram.callout.bracket_path


def test_a_negative_incremental_result_keeps_its_sign_in_the_label_but_not_the_geometry():
    """Treatment underperforming the holdout is a real possible finding.
    It must not draw a negative-height band, and it must not be silently
    relabelled as zero either."""
    report = build_report(_records(treated_recovered=8, holdout_recovered=15))
    assert report.incremental_paise < 0

    diagram = build_flow_diagram(report)

    assert _band(diagram, "t_incremental").height == 0.0
    assert diagram.incremental_is_negative is True
    assert diagram.incremental_label.startswith("-")


def test_an_empty_batch_renders_an_empty_diagram_rather_than_dividing_by_zero():
    diagram = build_flow_diagram(build_report([]))
    assert diagram.bands == ()
    assert diagram.ribbons == ()
    assert diagram.total_paise == 0


def test_every_ribbon_is_a_closed_path():
    diagram = _diagram()
    assert len(diagram.ribbons) == 7  # 2 from exposure, 3 from treated, 2 from holdout
    for ribbon in diagram.ribbons:
        assert ribbon.path.startswith("M ")
        assert ribbon.path.endswith("Z")


def test_the_bottom_arm_labels_below_itself_so_it_clears_the_ribbons():
    diagram = _diagram()
    holdout = _band(diagram, "holdout")
    assert holdout.label_below is True
    assert holdout.amount_y > holdout.y + holdout.height
    assert _band(diagram, "treated").amount_y < _band(diagram, "treated").y


# --- distribution and harm bars ----------------------------------------


def test_veto_segments_are_proportional_and_tile_the_full_width():
    bar = build_veto_distribution((("A", 60), ("B", 30), ("C", 10)), width=1000.0)
    assert bar.total == 100
    assert [segment.width for segment in bar.segments] == [600.0, 300.0, 100.0]
    assert bar.segments[-1].x + bar.segments[-1].width == pytest.approx(1000.0)


def test_veto_segment_opacity_descends_with_rank_but_stays_visible():
    bar = build_veto_distribution(tuple((f"CODE_{i}", 10) for i in range(9)))
    opacities = [segment.opacity for segment in bar.segments]
    assert opacities == sorted(opacities, reverse=True)
    assert min(opacities) >= 0.24


def test_an_empty_veto_distribution_has_no_segments():
    bar = build_veto_distribution(())
    assert bar.segments == ()
    assert bar.total == 0


def test_harm_bars_share_one_scale_across_metrics():
    """Normalising each metric to its own maximum would make a 1%
    complaint rate look as alarming as a 25% veto rate."""
    bars = build_harm_bars(build_report(_records()), width=200.0)
    ratios = [
        (bar.treated_width / bar.treated_rate) for bar in bars if bar.treated_rate > 0
    ]
    assert max(ratios) == pytest.approx(min(ratios), rel=1e-6)
    assert all(bar.treated_width <= 200.0 for bar in bars)


def test_a_zero_rate_draws_no_bar():
    bars = build_harm_bars(build_report(_records()))
    holdout_widths = [bar.holdout_width for bar in bars]
    assert holdout_widths == [0.0, 0.0, 0.0]  # holdout is never contacted


# --- interval plot -------------------------------------------------------


def test_the_interval_plot_places_zero_measured_and_truth_on_one_axis():
    plot = build_interval_plot(build_report(_records()))
    assert plot is not None
    assert 0 <= plot.ci_x < plot.ci_x + plot.ci_width <= plot.width
    assert plot.ci_x <= plot.measured_x <= plot.ci_x + plot.ci_width
    assert 0 <= plot.truth_x <= plot.width


def test_truth_inside_matches_the_reports_own_verdict():
    report = build_report(_records())
    plot = build_interval_plot(report)
    assert plot.truth_inside == bool(report.ground_truth.expected_within_ci)


def test_no_interval_plot_without_a_confidence_interval():
    report = build_report([r for r in _records() if r.arm == "treatment"])
    assert build_interval_plot(report) is None
