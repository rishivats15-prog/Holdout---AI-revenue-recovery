"""SVG geometry, computed in Python.

Every coordinate the templates draw is produced here, so the arithmetic
behind the scoreboard's flow diagram sits next to the arithmetic behind
the numbers it depicts — and is unit-testable in the same way. There is no
charting library, and no geometry is computed in JavaScript or in a Jinja
expression.

The templates receive plain dataclasses with ready-made `path` strings and
`x/y/width/height` numbers, and do nothing but interpolate them.
"""

from __future__ import annotations

from dataclasses import dataclass

from eval.money import format_paise, format_rate
from eval.report import BatchReport

# --- flow diagram canvas ------------------------------------------------

CANVAS_WIDTH = 1120.0
CANVAS_HEIGHT = 430.0
FLOW_TOP = 34.0
FLOW_HEIGHT = 352.0

NODE_WIDTH = 11.0
STAGE_X = (10.0, 372.0, 648.0)  # exposure -> arm -> outcome
LABEL_X = STAGE_X[2] + NODE_WIDTH + 14.0   # amount + descriptor for each band
BRACKET_X = 846.0                          # spans the incremental band
CALLOUT_X = BRACKET_X + 22.0               # the display-serif incremental figure

ARM_GAP = 12.0        # between the treated and holdout arm nodes
OUTCOME_GAP = 5.0     # between outcome bands
RECOVERED_SEAM = 0.0  # baseline and incremental are one band, subdivided


@dataclass(frozen=True)
class FlowBand:
    """One rectangle in a node column."""

    key: str
    label: str
    amount_label: str
    detail: str
    tone: str  # css modifier: exposure | treated | holdout | recovered | incremental | lost
    x: float
    y: float
    width: float
    height: float
    label_below: bool = False  # the bottom-most node labels under itself, not over

    @property
    def mid_y(self) -> float:
        return self.y + self.height / 2

    @property
    def amount_y(self) -> float:
        return (self.y + self.height + 18.0) if self.label_below else (self.y - 16.0)

    @property
    def detail_y(self) -> float:
        return self.amount_y + 12.0


@dataclass(frozen=True)
class FlowRibbon:
    key: str
    path: str
    tone: str


@dataclass(frozen=True)
class LabelAnchor:
    """One text label beside the outcome column. Anchored per outcome
    *group* rather than per band, so the treated arm's two recovered
    slices carry a single figure — their total — while the bracket below
    marks which part of it the experiment attributes to treatment."""

    amount: str
    descriptor: str
    y: float


@dataclass(frozen=True)
class Callout:
    """The incremental figure, anchored to the band it measures. A bracket
    spans exactly the incremental slice's height, so the display-serif
    number is visibly the size of that strip and not a caption floating
    near it."""

    bracket_path: str
    text_x: float
    text_y: float
    sub_y: float
    label: str
    sub_label: str


@dataclass(frozen=True)
class FlowDiagram:
    width: float
    height: float
    label_x: float
    bands: tuple[FlowBand, ...]
    ribbons: tuple[FlowRibbon, ...]
    incremental_band: FlowBand | None
    incremental_label: str
    incremental_is_negative: bool
    treated_rate_label: str
    holdout_rate_label: str
    total_paise: int
    callout: Callout | None = None
    label_anchors: tuple[LabelAnchor, ...] = ()


def build_flow_diagram(report: BatchReport) -> FlowDiagram:
    """Exposure at risk splits into treated and holdout; each splits into
    recovered and not. Band thickness is proportional to rupees throughout,
    and every band in a column sums exactly to its parent.

    The treated arm's recovered band is subdivided at the reported
    incremental figure, so the lower slice is literally the money the
    experiment attributes to treatment and the upper slice is what would
    have arrived anyway. That slice is drawn immediately above the
    holdout's recovered band — the two recovered bands adjacent, with the
    product of the whole system as the strip between them.
    """
    treated, holdout = report.treatment, report.holdout
    total_paise = treated.exposure_at_risk_paise + holdout.exposure_at_risk_paise
    if total_paise <= 0:
        return FlowDiagram(
            width=CANVAS_WIDTH, height=CANVAS_HEIGHT, label_x=LABEL_X, bands=(), ribbons=(),
            incremental_band=None, incremental_label=format_paise(0), incremental_is_negative=False,
            treated_rate_label=format_rate(0.0), holdout_rate_label=format_rate(0.0), total_paise=0,
        )

    # Incremental is clamped at zero for geometry only — a negative result
    # is a real possible finding, so it keeps its sign in the label.
    incremental_paise = max(report.incremental_paise, 0)
    incremental_paise = min(incremental_paise, treated.gross_recovered_paise)
    baseline_paise = treated.gross_recovered_paise - incremental_paise

    gaps = ARM_GAP + 3 * OUTCOME_GAP + RECOVERED_SEAM
    scale = (FLOW_HEIGHT - gaps) / total_paise

    def h(paise: int) -> float:
        return max(paise * scale, 0.0)

    # Stage 0 — everything at risk. Sits centred against the arm column.
    exposure_height = h(total_paise)
    exposure_y = FLOW_TOP + (FLOW_HEIGHT - exposure_height) / 2

    # Stage 1 — arms, treated on top.
    treated_y = FLOW_TOP
    treated_height = h(treated.exposure_at_risk_paise)
    holdout_y = treated_y + treated_height + ARM_GAP
    holdout_height = h(holdout.exposure_at_risk_paise)

    # Stage 2 — outcomes, ordered so the two recovered bands meet in the middle.
    t_lost_paise = treated.exposure_at_risk_paise - treated.gross_recovered_paise
    h_lost_paise = holdout.exposure_at_risk_paise - holdout.gross_recovered_paise

    treated_rate_label = format_rate(treated.recovery_rate, 1)
    holdout_rate_label = format_rate(holdout.recovery_rate, 1)

    cursor = FLOW_TOP
    outcome_specs = [
        ("t_lost", "Not recovered", "Treated", t_lost_paise, "lost", OUTCOME_GAP),
        ("t_baseline", "Recovered anyway", "Treated · baseline", baseline_paise, "recovered", RECOVERED_SEAM),
        ("t_incremental", "Recovered", f"Treated · {treated_rate_label} in all", incremental_paise, "incremental", OUTCOME_GAP),
        ("h_recovered", "Recovered", f"Holdout · {holdout_rate_label}", holdout.gross_recovered_paise, "recovered", OUTCOME_GAP),
        ("h_lost", "Not recovered", "Holdout", h_lost_paise, "lost", 0.0),
    ]

    bands: list[FlowBand] = [
        FlowBand(
            key="exposure", label="Exposure at risk", amount_label=format_paise(total_paise),
            detail=f"{report.total_cases} cases", tone="exposure",
            x=STAGE_X[0], y=exposure_y, width=NODE_WIDTH, height=exposure_height,
        ),
        FlowBand(
            key="treated", label="Treated", amount_label=format_paise(treated.exposure_at_risk_paise),
            detail=f"{treated.cases} cases", tone="treated",
            x=STAGE_X[1], y=treated_y, width=NODE_WIDTH, height=treated_height,
        ),
        FlowBand(
            key="holdout", label="Holdout", amount_label=format_paise(holdout.exposure_at_risk_paise),
            detail=f"{holdout.cases} cases", tone="holdout",
            x=STAGE_X[1], y=holdout_y, width=NODE_WIDTH, height=holdout_height, label_below=True,
        ),
    ]

    outcome_bands: dict[str, FlowBand] = {}
    for key, label, arm, paise, tone, gap_after in outcome_specs:
        height = h(paise)
        band = FlowBand(
            key=key, label=label, amount_label=format_paise(paise),
            detail=arm, tone=tone,
            x=STAGE_X[2], y=cursor, width=NODE_WIDTH, height=height,
        )
        outcome_bands[key] = band
        bands.append(band)
        cursor += height + gap_after

    ribbons = [
        FlowRibbon("exposure_treated", _ribbon(
            STAGE_X[0] + NODE_WIDTH, exposure_y, STAGE_X[1], treated_y, treated_height, treated_height,
        ), "treated"),
        FlowRibbon("exposure_holdout", _ribbon(
            STAGE_X[0] + NODE_WIDTH, exposure_y + treated_height, STAGE_X[1], holdout_y, holdout_height, holdout_height,
        ), "holdout"),
    ]

    # Sources stack inside each arm node in the same order as their
    # targets, so no two ribbons from one arm ever cross.
    source_cursor = treated_y
    for key in ("t_lost", "t_baseline", "t_incremental"):
        band = outcome_bands[key]
        ribbons.append(FlowRibbon(
            f"treated_{key}",
            _ribbon(STAGE_X[1] + NODE_WIDTH, source_cursor, STAGE_X[2], band.y, band.height, band.height),
            band.tone,
        ))
        source_cursor += band.height

    source_cursor = holdout_y
    for key in ("h_recovered", "h_lost"):
        band = outcome_bands[key]
        ribbons.append(FlowRibbon(
            f"holdout_{key}",
            _ribbon(STAGE_X[1] + NODE_WIDTH, source_cursor, STAGE_X[2], band.y, band.height, band.height),
            band.tone,
        ))
        source_cursor += band.height

    incremental_band = outcome_bands["t_incremental"]
    baseline_band = outcome_bands["t_baseline"]
    recovered_group_mid = (baseline_band.y + incremental_band.y + incremental_band.height) / 2

    label_anchors = (
        LabelAnchor(format_paise(t_lost_paise), "Not recovered · treated", outcome_bands["t_lost"].mid_y),
        LabelAnchor(
            format_paise(treated.gross_recovered_paise),
            f"Recovered · treated {treated_rate_label}",
            recovered_group_mid,
        ),
        LabelAnchor(
            format_paise(holdout.gross_recovered_paise),
            f"Recovered · holdout {holdout_rate_label}",
            outcome_bands["h_recovered"].mid_y,
        ),
        LabelAnchor(format_paise(h_lost_paise), "Not recovered · holdout", outcome_bands["h_lost"].mid_y),
    )

    return FlowDiagram(
        width=CANVAS_WIDTH,
        height=CANVAS_HEIGHT,
        label_x=LABEL_X,
        bands=tuple(bands),
        ribbons=tuple(ribbons),
        incremental_band=incremental_band,
        incremental_label=format_paise(report.incremental_paise),
        incremental_is_negative=report.incremental_paise < 0,
        treated_rate_label=treated_rate_label,
        holdout_rate_label=holdout_rate_label,
        total_paise=total_paise,
        callout=_callout(incremental_band, format_paise(report.incremental_paise)),
        label_anchors=label_anchors,
    )


def _callout(band: FlowBand, label: str) -> Callout:
    """A square bracket hugging the incremental band, with the figure set
    beside it."""
    top, bottom = band.y, band.y + band.height
    arm = 7.0
    return Callout(
        bracket_path=(
            f"M {BRACKET_X + arm:.2f},{top:.2f} L {BRACKET_X:.2f},{top:.2f} "
            f"L {BRACKET_X:.2f},{bottom:.2f} L {BRACKET_X + arm:.2f},{bottom:.2f}"
        ),
        text_x=CALLOUT_X,
        text_y=band.mid_y + 4.0,
        sub_y=band.mid_y + 20.0,
        label=label,
        sub_label="INCREMENTAL — ATTRIBUTED TO TREATMENT",
    )


def _ribbon(x0: float, y0: float, x1: float, y1: float, h0: float, h1: float) -> str:
    """A filled cubic ribbon from (x0, y0) with thickness h0 to (x1, y1)
    with thickness h1. Control points sit halfway across, which is what
    gives a Sankey its characteristic flat-then-turn shape."""
    cx = (x0 + x1) / 2
    return (
        f"M {x0:.2f},{y0:.2f} "
        f"C {cx:.2f},{y0:.2f} {cx:.2f},{y1:.2f} {x1:.2f},{y1:.2f} "
        f"L {x1:.2f},{y1 + h1:.2f} "
        f"C {cx:.2f},{y1 + h1:.2f} {cx:.2f},{y0 + h0:.2f} {x0:.2f},{y0 + h0:.2f} Z"
    )


# --- distribution bars --------------------------------------------------


@dataclass(frozen=True)
class BarSegment:
    label: str
    count: int
    share: float
    x: float
    width: float
    opacity: float


@dataclass(frozen=True)
class DistributionBar:
    width: float
    height: float
    segments: tuple[BarSegment, ...]
    total: int


def build_veto_distribution(counts: tuple[tuple[str, int], ...], width: float = 1000.0, height: float = 30.0) -> DistributionBar:
    """One horizontal bar, segments proportional to how often each reason
    code fired, in --veto at descending opacity. Opacity ranks the codes;
    width carries the quantity, so the bar stays readable in greyscale."""
    total = sum(count for _, count in counts)
    if total <= 0:
        return DistributionBar(width=width, height=height, segments=(), total=0)

    segments: list[BarSegment] = []
    cursor = 0.0
    for index, (label, count) in enumerate(counts):
        share = count / total
        segment_width = share * width
        segments.append(
            BarSegment(
                label=label, count=count, share=share, x=cursor, width=segment_width,
                opacity=round(max(0.92 - index * 0.13, 0.24), 2),
            )
        )
        cursor += segment_width
    return DistributionBar(width=width, height=height, segments=tuple(segments), total=total)


# --- paired harm bars ---------------------------------------------------


@dataclass(frozen=True)
class PairedBar:
    label: str
    treated_rate: float
    holdout_rate: float
    treated_label: str
    holdout_label: str
    treated_width: float
    holdout_width: float
    scale_label: str


def build_harm_bars(report: BatchReport, width: float = 168.0) -> tuple[PairedBar, ...]:
    """Treated vs holdout on each harm metric, drawn against a shared
    scale so the bars are comparable to each other rather than each
    normalised to its own maximum — normalising per-metric would make a
    0.1% complaint rate look as alarming as a 25% veto rate."""
    metrics = (
        ("Opt-out rate", report.treatment.opt_out_rate, report.holdout.opt_out_rate),
        ("Complaint rate", report.treatment.complaint_rate, report.holdout.complaint_rate),
        ("Policy-veto rate", report.treatment.veto_rate, report.holdout.veto_rate),
    )
    ceiling = max([value for _, treated, holdout in metrics for value in (treated, holdout)] + [0.05])
    scale_label = format_rate(ceiling, 0)

    return tuple(
        PairedBar(
            label=label,
            treated_rate=treated,
            holdout_rate=holdout,
            treated_label=format_rate(treated),
            holdout_label=format_rate(holdout),
            treated_width=round(treated / ceiling * width, 2),
            holdout_width=round(holdout / ceiling * width, 2),
            scale_label=scale_label,
        )
        for label, treated, holdout in metrics
    )


# --- ground-truth comparison marker -------------------------------------


@dataclass(frozen=True)
class IntervalPlot:
    """The measured confidence interval with the known true effect marked
    on it — the scoreboard's proof that the measurement validates itself.
    Coordinates are fractions of the plot width, resolved in the template."""

    width: float
    height: float
    axis_min: float
    axis_max: float
    ci_x: float
    ci_width: float
    measured_x: float
    truth_x: float
    zero_x: float
    truth_inside: bool
    measured_label: str
    truth_label: str
    lower_label: str
    upper_label: str


def build_interval_plot(report: BatchReport, width: float = 440.0, height: float = 52.0) -> IntervalPlot | None:
    ci = report.incremental_ci
    ground_truth = report.ground_truth
    if ci is None or ground_truth is None or ground_truth.expected_incremental_rate is None:
        return None

    truth = ground_truth.expected_incremental_rate
    span = max(ci.upper, truth, 0.0) - min(ci.lower, truth, 0.0)
    padding = span * 0.12 if span else 0.01
    axis_min = min(ci.lower, truth, 0.0) - padding
    axis_max = max(ci.upper, truth, 0.0) + padding

    def to_x(value: float) -> float:
        return round((value - axis_min) / (axis_max - axis_min) * width, 2)

    return IntervalPlot(
        width=width,
        height=height,
        axis_min=axis_min,
        axis_max=axis_max,
        ci_x=to_x(ci.lower),
        ci_width=round(to_x(ci.upper) - to_x(ci.lower), 2),
        measured_x=to_x(ci.delta),
        truth_x=to_x(truth),
        zero_x=to_x(0.0),
        truth_inside=bool(ground_truth.expected_within_ci),
        measured_label=format_rate(ci.delta),
        truth_label=format_rate(truth),
        lower_label=format_rate(ci.lower),
        upper_label=format_rate(ci.upper),
    )


# --- live run progression ------------------------------------------------


@dataclass(frozen=True)
class RunSeries:
    label: str
    tone: str  # recovered | accent | veto
    points: str  # an SVG polyline `points` attribute
    final_value: int
    final_x: float
    final_y: float


@dataclass(frozen=True)
class RunChart:
    title: str
    scale_note: str
    width: float
    height: float
    series: tuple[RunSeries, ...]
    day_ticks: tuple[tuple[float, str], ...]
    max_value: int
    baseline_y: float


@dataclass(frozen=True)
class RunProgression:
    """Two charts, not one.

    Cases recovered and actions attempted are different units, and putting
    them on a single axis is a category error rather than a fairness: a
    few hundred recoveries against a few thousand touches flattens the
    recovery line to the baseline and hides the thing the page exists to
    show. Each chart is internally comparable instead — recovered against
    the case count, sent against vetoed — and each says which scale it is
    on.
    """

    days: int
    recoveries: RunChart
    actions: RunChart


PAD_TOP = 12.0
PAD_BOTTOM = 24.0
PAD_X = 14.0


def build_run_progression(
    history, total_ticks: int, total_cases: int = 0, width: float = 490.0, height: float = 168.0
) -> RunProgression | None:
    """`history` is a list of orchestrator TickResult. Returns None before
    the first day has run — there is no line to draw through zero points,
    and the page shows an empty state instead."""
    if not history:
        return None

    cumulative = {"recovered": [], "executed": [], "vetoed": []}
    running = {"recovered": 0, "executed": 0, "vetoed": 0}
    for result in history:
        running["recovered"] += result.recovered
        running["executed"] += result.executed
        running["vetoed"] += result.vetoed
        for key in cumulative:
            cumulative[key].append(running[key])

    # x is laid out against the FULL run length, not the days elapsed, so
    # the lines advance across the chart as the run proceeds instead of
    # rescaling under the viewer on every click.
    span = max(total_ticks - 1, 1)

    def x_for(index: int) -> float:
        return PAD_X + (index / span) * (width - PAD_X * 2)

    step = max(1, total_ticks // 6)
    day_ticks = tuple((x_for(index), f"D{index + 1}") for index in range(0, total_ticks, step))

    def chart(title: str, scale_note: str, specs, ceiling: int) -> RunChart:
        plot_height = height - PAD_TOP - PAD_BOTTOM
        baseline_y = height - PAD_BOTTOM
        top = max(ceiling, 1)

        def y_for(value: int) -> float:
            return baseline_y - (value / top) * plot_height

        series = []
        for key, label, tone in specs:
            values = cumulative[key]
            series.append(RunSeries(
                label=label, tone=tone,
                points=" ".join(f"{x_for(i):.2f},{y_for(v):.2f}" for i, v in enumerate(values)),
                final_value=values[-1], final_x=x_for(len(values) - 1), final_y=y_for(values[-1]),
            ))
        return RunChart(
            title=title, scale_note=scale_note, width=width, height=height,
            series=tuple(series), day_ticks=day_ticks, max_value=top, baseline_y=baseline_y,
        )

    # Recoveries are scaled against the whole batch, so the line's height
    # reads as "how much of the book has come back" rather than against a
    # moving maximum that makes any run look equally successful.
    recovery_ceiling = total_cases or max(cumulative["recovered"][-1], 1)
    action_ceiling = max(cumulative["executed"][-1], cumulative["vetoed"][-1], 1)

    return RunProgression(
        days=len(history),
        recoveries=chart(
            "Cases recovered", f"of {recovery_ceiling} cases",
            (("recovered", "Recovered", "recovered"),), recovery_ceiling,
        ),
        actions=chart(
            "Actions attempted", "sent and vetoed, same scale",
            (("executed", "Touches sent", "accent"), ("vetoed", "Vetoed", "veto")), action_ceiling,
        ),
    )
