"""Renders a BatchReport as plain text for the terminal. Formatting only —
every number printed here was computed in eval/report.py; nothing is
derived, rounded into, or recombined at render time.

Phase 7's dashboard renders the same BatchReport object through Jinja
instead, so a figure can never disagree between the CLI and the
scoreboard.
"""

from __future__ import annotations

from eval.money import format_paise, format_rate
from eval.report import BatchReport

WIDTH = 78


def render_report(report: BatchReport) -> str:
    lines: list[str] = []
    out = lines.append

    out(_rule("="))
    out("AI REVENUE RECOVERY — BATCH REPORT")
    out(
        f"SYNTHETIC · GROUND TRUTH KNOWN    WINDOW {report.attribution_window_days}D    "
        f"{report.generated_at.strftime('%Y-%m-%d %H:%M')}"
    )
    out(_rule("="))
    out("")

    _headline(out, report)
    _ground_truth(out, report)
    _arms(out, report)
    _harm(out, report)
    _cause_table(out, report)
    _channel_table(out, report)
    _vetoes(out, report)
    _footnotes(out, report)

    return "\n".join(lines)


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _section(out, title: str) -> None:
    out("")
    out(title.upper())
    out(_rule())


def _headline(out, report: BatchReport) -> None:
    _section(out, "Headline — incremental recovery")
    ci = report.incremental_ci
    ci_text = (
        f"95% CI [{format_rate(ci.lower)}, {format_rate(ci.upper)}]" if ci else "95% CI unavailable (an arm is empty)"
    )
    out(f"  Incremental recovery rate   {format_rate(report.incremental_rate)}   {ci_text}")
    out(f"  Incremental recovered       {format_paise(report.incremental_paise)}")
    out(f"  Net of channel cost         {format_paise(report.net_incremental_paise)}")
    out(f"  Total channel cost          {format_paise(report.total_cost_paise, paise_precision=True)}")
    out(
        f"  Cost per ₹100 incremental   {_paise_per_100(report.cost_per_100_incremental_paise)}"
        f"      (per ₹100 gross: {_paise_per_100(report.cost_per_100_gross_paise)})"
    )
    out("")
    out(
        f"  Gross recovered, treated arm, is {format_paise(report.treatment.gross_recovered_paise)} — but most of that"
    )
    out("  money would have arrived anyway. The incremental figure above is the product.")


def _ground_truth(out, report: BatchReport) -> None:
    gt = report.ground_truth
    if gt is None:
        return
    _section(out, "Ground-truth check — is the measurement honest?")
    out(f"  Measured incremental rate   {format_rate(gt.measured_incremental_rate)}")
    out(f"  True simulated effect       {format_rate(gt.expected_incremental_rate)}")
    out(f"  Absolute error              {format_rate(gt.absolute_error)}")
    if gt.expected_within_ci is None:
        verdict = "NO CI — cannot check"
    else:
        verdict = "PASS — true effect lies inside the measured CI" if gt.expected_within_ci else "FAIL — true effect lies OUTSIDE the measured CI"
    out(f"  Verdict                     {verdict}")
    out("")
    out(f"  Per-arm rates      measured   expected")
    out(f"    treatment        {format_rate(report.treatment.recovery_rate):>8}   {format_rate(gt.expected_treatment_rate):>8}")
    out(f"    holdout          {format_rate(report.holdout.recovery_rate):>8}   {format_rate(gt.expected_holdout_rate):>8}")
    out("")
    out(f"  The YAML's nominal uplift for this case mix is {format_rate(gt.nominal_mean_uplift)}, but only")
    out(
        f"  {gt.window_factor:.0%} of simulated recoveries land inside the {report.attribution_window_days}-day window, so the largest"
    )
    out(f"  effect this report could ever measure is {format_rate(gt.expected_incremental_rate)}.")


def _arms(out, report: BatchReport) -> None:
    _section(out, "Arms")
    header = f"  {'':<12}{'cases':>7}{'recovered':>11}{'rate':>9}{'exposure':>14}{'gross in':>14}"
    out(header)
    for arm in (report.treatment, report.holdout):
        out(
            f"  {arm.arm:<12}{arm.cases:>7}{arm.recovered:>11}{format_rate(arm.recovery_rate):>9}"
            f"{format_paise(arm.exposure_at_risk_paise):>14}{format_paise(arm.gross_recovered_paise):>14}"
        )
    out("")
    out(f"  {'':<12}{'touches':>9}{'per recovery':>14}{'median days':>13}{'cost':>12}")
    for arm in (report.treatment, report.holdout):
        per_recovery = f"{arm.touches_per_recovery:.2f}" if arm.touches_per_recovery is not None else "—"
        days = f"{arm.median_days_to_cash:.1f}" if arm.median_days_to_cash is not None else "—"
        out(
            f"  {arm.arm:<12}{arm.executed_touches:>9}{per_recovery:>14}{days:>13}"
            f"{format_paise(arm.cost_paise, paise_precision=True):>12}"
        )
    out("")
    out(f"  {report.total_cases} assigned cases across {report.strata} strata"
        + (f"; {report.unassigned_cases} unassigned cases excluded" if report.unassigned_cases else ""))


def _harm(out, report: BatchReport) -> None:
    _section(out, "Harm metrics — treated vs holdout")
    out(f"  {'':<12}{'opt-out':>10}{'complaint':>12}{'policy veto':>14}")
    for arm in (report.treatment, report.holdout):
        veto = format_rate(arm.veto_rate) if arm.total_actions else "n/a"
        out(f"  {arm.arm:<12}{format_rate(arm.opt_out_rate):>10}{format_rate(arm.complaint_rate):>12}{veto:>14}")
    out("")
    out("  The holdout's zeroes are structural, not measured: nothing was ever sent to")
    out("  it, so it cannot generate a complaint or a veto. That asymmetry IS the harm")
    out("  finding — treatment buys its lift at the cost of the rates on the top row.")


def _cause_table(out, report: BatchReport) -> None:
    _section(out, "Per-cause lift (grouped by diagnosed cause)")
    out(f"  {'cause':<24}{'T n':>6}{'T rate':>9}{'H n':>6}{'H rate':>9}{'lift':>9}{'incr ₹':>13}")
    for lift in report.cause_lifts:
        out(
            f"  {lift.cause:<24}{lift.treatment_cases:>6}{format_rate(lift.treatment_rate, 1):>9}"
            f"{lift.holdout_cases:>6}{format_rate(lift.holdout_rate, 1):>9}"
            f"{format_rate(lift.incremental_rate, 1):>9}{format_paise(lift.incremental_paise):>13}"
        )
    out("")
    out("  Per-cause strata are small, so individual rows are noisy — the batch-level")
    out("  figure above is the one with a confidence interval attached.")


def _channel_table(out, report: BatchReport) -> None:
    if not report.channel_lifts:
        return
    _section(out, "Per-channel recovery (descriptive, not causal)")
    out(f"  {'channel':<14}{'touched':>9}{'recovered':>11}{'rate':>9}{'vs holdout':>12}{'cost':>12}")
    for lift in report.channel_lifts:
        out(
            f"  {lift.channel:<14}{lift.cases_touched:>9}{lift.recovered:>11}{format_rate(lift.recovery_rate, 1):>9}"
            f"{format_rate(lift.delta_vs_holdout, 1):>12}{format_paise(lift.cost_paise, paise_precision=True):>12}"
        )
    out("")
    out("  Channel is chosen by the ladder, not randomized, and a case can appear on")
    out("  several rows. Read these as where the money showed up, never as what caused it.")


def _vetoes(out, report: BatchReport) -> None:
    if not report.veto_reason_counts:
        return
    _section(out, "Policy vetoes by reason code")
    total = sum(count for _, count in report.veto_reason_counts)
    for code, count in report.veto_reason_counts:
        bar = "#" * round(count / total * 34) if total else ""
        out(f"  {code:<32}{count:>6}  {bar}")
    out(f"  {'TOTAL':<32}{total:>6}")


def _footnotes(out, report: BatchReport) -> None:
    accuracy = report.diagnosis_accuracy
    _section(out, "Notes")
    out(f"  · Attribution window fixed at {report.attribution_window_days} days from detection. Payments outside it")
    out("    are not counted, even where the case row says recovered.")
    out("  · Analysis is intent-to-treat: every case assigned to treatment stays in the")
    out("    denominator, including ones the policy gate suppressed or wrote off.")
    if accuracy is not None:
        out(
            f"  · Diagnosis accuracy against ground truth: {format_rate(accuracy.accuracy, 1)} "
            f"({accuracy.correct}/{accuracy.cases_with_true_cause})."
        )
    out("  · Data is synthetic end to end. That is what makes the ground-truth check")
    out("    above possible — a live gateway offers no true effect to check against.")


def _paise_per_100(value: float | None) -> str:
    return "—" if value is None else f"₹{value / 100:.2f}"
