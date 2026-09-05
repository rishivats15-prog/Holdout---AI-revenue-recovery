"""eval/render.py — the text renderer. Formatting only, so these check
that the honest framing survives rendering: the incremental figure is
present, the ground-truth verdict is stated in words, and the holdout's
structurally-zero harm rates are labelled rather than left to imply the
treatment arm is uniquely harmful.
"""

from __future__ import annotations

import dataclasses
import json

from db.enums import CaseState
from eval.render import render_report
from eval.report import CaseRecord, build_report


def _record(case_id: int, arm: str, cause: str, *, recovered: bool, days: int | None = None, **kwargs) -> CaseRecord:
    return CaseRecord(
        case_id=case_id, arm=arm, stratum=f"{cause}:mid", diagnosed_cause=cause, true_cause=cause,
        exposure_paise=100_000, state=kwargs.pop("state", CaseState.IN_TREATMENT.value),
        recovered_in_window=recovered, days_to_cash=days if recovered else None,
        executed_touches=kwargs.pop("touches", 0), vetoed_actions=kwargs.pop("vetoed", 0),
        cost_paise=kwargs.pop("cost", 0), channels_delivered=kwargs.pop("channels", ()),
        complained=kwargs.pop("complained", False),
    )


def _report():
    treated = [
        _record(i, "treatment", "balance_timing", recovered=i < 12, days=5, touches=2, cost=150, channels=("sms",))
        for i in range(30)
    ]
    control = [_record(100 + i, "holdout", "balance_timing", recovered=i < 2, days=8) for i in range(10)]
    return build_report(treated + control, veto_reason_counts=(("CUSTOMER_DND", 4), ("RUNG_COOLDOWN_ACTIVE", 2)))


def test_render_produces_every_section():
    text = render_report(_report())
    for heading in (
        "HEADLINE — INCREMENTAL RECOVERY",
        "GROUND-TRUTH CHECK",
        "ARMS",
        "HARM METRICS",
        "PER-CAUSE LIFT",
        "PER-CHANNEL RECOVERY",
        "POLICY VETOES BY REASON CODE",
        "NOTES",
    ):
        assert heading in text


def test_render_carries_the_synthetic_marker():
    assert "SYNTHETIC · GROUND TRUTH KNOWN" in render_report(_report())


def test_render_states_the_ground_truth_verdict_in_words():
    text = render_report(_report())
    assert ("PASS —" in text) or ("FAIL —" in text)


def test_render_labels_the_holdout_zeroes_as_structural():
    assert "structural, not measured" in render_report(_report())


def test_render_marks_the_channel_table_as_non_causal():
    text = render_report(_report())
    assert "not causal" in text.lower()


def test_render_survives_a_report_with_no_channels_or_vetoes():
    report = build_report(
        [_record(1, "treatment", "risk_fraud", recovered=False), _record(2, "holdout", "risk_fraud", recovered=False)]
    )
    text = render_report(report)
    assert "PER-CHANNEL RECOVERY" not in text
    assert "POLICY VETOES" not in text
    assert "—" in text  # the undefined cost-per-₹100 renders as a dash, not an error


def test_report_is_json_serializable_for_the_dashboard():
    """Phase 7 renders this same object; asdict + a str default has to
    survive the dataclass nesting and the datetime."""
    payload = json.dumps(dataclasses.asdict(_report()), default=str)
    assert "incremental_rate" in payload
    assert "ground_truth" in payload
