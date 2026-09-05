"""The batch report — the headline number and everything needed to argue
it's honest.

Split deliberately in two:

  * `load_case_records` is the only part that touches the database. It
    flattens each case and everything that happened to it into one plain
    `CaseRecord`.
  * `build_report` is pure arithmetic over a list of those records — no
    session, no queries. Every figure in the report can therefore be
    tested against hand-built records with a hand-computed answer, which
    is the point: the measurement layer is exactly the part of this
    system that must not be taken on trust.

Nothing here calls an LLM, and nothing here reads a diagnosis to decide
anything — the diagnosed cause is a grouping key for the per-cause lift
table, and the true cause is used only by eval/groundtruth.py's
prediction.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from db.enums import CaseState, ExperimentArm, OutcomeType, PolicyVerdict
from db.models import Action, Case, Diagnosis, ExperimentAssignment, Outcome, utcnow
from eval.attribution import ATTRIBUTION_WINDOW_DAYS, days_to_cash, is_within_window
from eval.groundtruth import GroundTruthCheck, check_against_ground_truth
from eval.metrics import ProportionCI, median, rate, two_proportion_ci


@dataclass(frozen=True)
class CaseRecord:
    """One case, flattened. Everything build_report needs and nothing else."""

    case_id: int
    arm: str
    stratum: str
    diagnosed_cause: str
    true_cause: str | None
    exposure_paise: int
    state: str
    recovered_in_window: bool
    days_to_cash: int | None  # None unless the payment landed inside the window
    executed_touches: int
    vetoed_actions: int
    cost_paise: int
    channels_delivered: tuple[str, ...] = ()
    complained: bool = False

    @property
    def total_actions(self) -> int:
        return self.executed_touches + self.vetoed_actions

    @property
    def opted_out(self) -> bool:
        """Suppressed is the state machine's own term for opt-out or cap."""
        return self.state == CaseState.SUPPRESSED.value


@dataclass(frozen=True)
class ArmSummary:
    arm: str
    cases: int
    exposure_at_risk_paise: int
    mean_exposure_paise: int
    recovered: int
    recovery_rate: float
    gross_recovered_paise: int
    executed_touches: int
    vetoed_actions: int
    total_actions: int
    cost_paise: int
    veto_rate: float
    opt_out_rate: float
    complaint_rate: float
    median_days_to_cash: float | None
    touches_per_recovery: float | None


@dataclass(frozen=True)
class CauseLift:
    """Grouped by DIAGNOSED cause — what an operator can actually see and
    act on. The ground-truth check groups by true cause instead; when the
    diagnosis engine is doing its job the two agree closely, and where
    they don't, that's a diagnosis-accuracy story, not a lift story."""

    cause: str
    treatment_cases: int
    treatment_recovered: int
    treatment_rate: float
    holdout_cases: int
    holdout_recovered: int
    holdout_rate: float
    incremental_rate: float
    incremental_paise: int


@dataclass(frozen=True)
class ChannelLift:
    """Descriptive, NOT causal. Channel is chosen by the ladder, so which
    cases got a WhatsApp message is a consequence of their cause, rung and
    consent flags — not a randomized arm. The delta against the holdout is
    reported because it's informative about where the money shows up, but
    it must never be read as "WhatsApp caused this lift"; only the
    treatment/holdout split supports a causal claim."""

    channel: str
    cases_touched: int
    recovered: int
    recovery_rate: float
    delta_vs_holdout: float
    cost_paise: int


@dataclass(frozen=True)
class DiagnosisAccuracy:
    """Not a headline metric — a guard rail. If the diagnosis engine were
    badly wrong, the per-cause lift table would be grouping cases by a
    label unrelated to their behaviour, and its rows would look flat for
    reasons that have nothing to do with the treatment."""

    cases_with_true_cause: int
    correct: int
    accuracy: float


@dataclass(frozen=True)
class BatchReport:
    generated_at: dt.datetime
    attribution_window_days: int
    total_cases: int
    unassigned_cases: int
    strata: int
    treatment: ArmSummary
    holdout: ArmSummary

    incremental_rate: float
    incremental_ci: ProportionCI | None
    incremental_paise: int
    total_cost_paise: int
    net_incremental_paise: int
    cost_per_100_incremental_paise: float | None
    cost_per_100_gross_paise: float | None

    cause_lifts: tuple[CauseLift, ...] = field(default_factory=tuple)
    channel_lifts: tuple[ChannelLift, ...] = field(default_factory=tuple)
    veto_reason_counts: tuple[tuple[str, int], ...] = field(default_factory=tuple)
    diagnosis_accuracy: DiagnosisAccuracy | None = None
    ground_truth: GroundTruthCheck | None = None


# ---------------------------------------------------------------- loading


def load_case_records(db: Session) -> tuple[list[CaseRecord], int]:
    """Flattens every assigned case into a CaseRecord. Returns the records
    plus the count of cases with no experiment assignment — those are
    excluded from the report entirely, because a case that was never
    randomized belongs to neither arm and counting it in either would bias
    the comparison."""
    assignments = {row.case_id: row for row in db.query(ExperimentAssignment).all()}
    diagnoses = _latest_diagnosis_by_case(db)
    payments, complaints = _outcomes_by_case(db)
    action_stats = _action_stats_by_case(db)

    records: list[CaseRecord] = []
    unassigned = 0

    for case in db.query(Case).all():
        assignment = assignments.get(case.id)
        diagnosis = diagnoses.get(case.id)
        if assignment is None or diagnosis is None:
            unassigned += 1
            continue

        in_window_days = _first_in_window_payment_day(case, payments.get(case.id, []))
        stats = action_stats.get(case.id, _EMPTY_ACTION_STATS)

        records.append(
            CaseRecord(
                case_id=case.id,
                arm=assignment.arm,
                stratum=assignment.stratum,
                diagnosed_cause=diagnosis.cause,
                true_cause=case.true_root_cause,
                exposure_paise=case.exposure_amount,
                state=case.state,
                recovered_in_window=in_window_days is not None,
                days_to_cash=in_window_days,
                executed_touches=stats["executed"],
                vetoed_actions=stats["vetoed"],
                cost_paise=stats["cost"],
                channels_delivered=stats["channels"],
                complained=case.id in complaints,
            )
        )

    return records, unassigned


_EMPTY_ACTION_STATS = {"executed": 0, "vetoed": 0, "cost": 0, "channels": ()}


def _latest_diagnosis_by_case(db: Session) -> dict[int, Diagnosis]:
    latest: dict[int, Diagnosis] = {}
    for diagnosis in db.query(Diagnosis).order_by(Diagnosis.id).all():
        latest[diagnosis.case_id] = diagnosis  # ascending id — last write wins
    return latest


def _outcomes_by_case(db: Session) -> tuple[dict[int, list[Outcome]], set[int]]:
    payments: dict[int, list[Outcome]] = {}
    complaints: set[int] = set()
    for outcome in db.query(Outcome).all():
        if outcome.outcome_type == OutcomeType.PAYMENT.value:
            payments.setdefault(outcome.case_id, []).append(outcome)
        elif outcome.outcome_type == OutcomeType.DISPUTE.value:
            complaints.add(outcome.case_id)
    return payments, complaints


def _action_stats_by_case(db: Session) -> dict[int, dict]:
    stats: dict[int, dict] = {}
    for action in db.query(Action).order_by(Action.id).all():
        entry = stats.setdefault(action.case_id, {"executed": 0, "vetoed": 0, "cost": 0, "channels": []})
        if action.policy_verdict == PolicyVerdict.ALLOWED.value:
            entry["executed"] += 1
            entry["cost"] += action.cost
            if action.delivered and action.channel is not None and action.channel not in entry["channels"]:
                entry["channels"].append(action.channel)
        else:
            entry["vetoed"] += 1
    return {case_id: {**entry, "channels": tuple(entry["channels"])} for case_id, entry in stats.items()}


def _first_in_window_payment_day(case: Case, payments: list[Outcome]) -> int | None:
    """The attribution rule, applied. A payment outside 14 days from
    detection is never counted — not as a late recovery, not at a
    discount. The case row may well say `recovered`; the report does not."""
    in_window = [p for p in payments if is_within_window(case.detected_at, p.occurred_at)]
    if not in_window:
        return None
    earliest = min(in_window, key=lambda p: p.occurred_at)
    return days_to_cash(case.detected_at, earliest.occurred_at)


def load_veto_reason_counts(db: Session) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for action in db.query(Action).filter(Action.policy_verdict == PolicyVerdict.VETOED.value).all():
        code = action.veto_reason_code or "UNSPECIFIED"
        counts[code] = counts.get(code, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


# ---------------------------------------------------------------- building


def summarize_arm(arm: str, records: list[CaseRecord]) -> ArmSummary:
    n = len(records)
    recovered = [r for r in records if r.recovered_in_window]
    executed = sum(r.executed_touches for r in records)
    vetoed = sum(r.vetoed_actions for r in records)
    total_actions = executed + vetoed
    exposure = sum(r.exposure_paise for r in records)

    return ArmSummary(
        arm=arm,
        cases=n,
        exposure_at_risk_paise=exposure,
        mean_exposure_paise=round(exposure / n) if n else 0,
        recovered=len(recovered),
        recovery_rate=rate(len(recovered), n),
        gross_recovered_paise=sum(r.exposure_paise for r in recovered),
        executed_touches=executed,
        vetoed_actions=vetoed,
        total_actions=total_actions,
        cost_paise=sum(r.cost_paise for r in records),
        veto_rate=rate(vetoed, total_actions),
        opt_out_rate=rate(sum(1 for r in records if r.opted_out), n),
        complaint_rate=rate(sum(1 for r in records if r.complained), n),
        median_days_to_cash=median([float(r.days_to_cash) for r in recovered if r.days_to_cash is not None]),
        touches_per_recovery=(executed / len(recovered)) if recovered else None,
    )


def build_report(
    records: list[CaseRecord],
    *,
    unassigned_cases: int = 0,
    veto_reason_counts: tuple[tuple[str, int], ...] = (),
    generated_at: dt.datetime | None = None,
) -> BatchReport:
    treated = [r for r in records if r.arm == ExperimentArm.TREATMENT.value]
    control = [r for r in records if r.arm == ExperimentArm.HOLDOUT.value]

    treatment = summarize_arm(ExperimentArm.TREATMENT.value, treated)
    holdout = summarize_arm(ExperimentArm.HOLDOUT.value, control)

    incremental_rate = treatment.recovery_rate - holdout.recovery_rate
    ci = None
    if treated and control:
        ci = two_proportion_ci(treatment.recovered, treatment.cases, holdout.recovered, holdout.cases)

    # Incremental rupees, per CLAUDE.md: the rate delta x treated case
    # count x mean treated exposure. Deliberately not "sum of recovered
    # exposures minus what the holdout would have got" — that variant
    # quietly credits treatment with the exposure mix of whichever cases
    # happened to recover, which is not what was randomized.
    incremental_paise = round(incremental_rate * treatment.cases * treatment.mean_exposure_paise)
    total_cost_paise = treatment.cost_paise + holdout.cost_paise

    return BatchReport(
        generated_at=generated_at or utcnow(),
        attribution_window_days=ATTRIBUTION_WINDOW_DAYS,
        total_cases=len(records),
        unassigned_cases=unassigned_cases,
        strata=len({r.stratum for r in records}),
        treatment=treatment,
        holdout=holdout,
        incremental_rate=incremental_rate,
        incremental_ci=ci,
        incremental_paise=incremental_paise,
        total_cost_paise=total_cost_paise,
        net_incremental_paise=incremental_paise - total_cost_paise,
        cost_per_100_incremental_paise=_cost_per_100(total_cost_paise, incremental_paise),
        cost_per_100_gross_paise=_cost_per_100(total_cost_paise, treatment.gross_recovered_paise),
        cause_lifts=_cause_lifts(treated, control),
        channel_lifts=_channel_lifts(treated, holdout.recovery_rate),
        veto_reason_counts=veto_reason_counts,
        diagnosis_accuracy=_diagnosis_accuracy(records),
        ground_truth=_ground_truth_check(treated, control, incremental_rate, ci),
    )


def _cost_per_100(cost_paise: int, recovered_paise: int) -> float | None:
    """Paise spent per ₹100 recovered. None when nothing was recovered —
    a divide-by-zero dressed up as infinity would just be noise."""
    if recovered_paise <= 0:
        return None
    return cost_paise / recovered_paise * 10_000


def _cause_lifts(treated: list[CaseRecord], control: list[CaseRecord]) -> tuple[CauseLift, ...]:
    lifts = []
    for cause in sorted({r.diagnosed_cause for r in treated + control}):
        t = [r for r in treated if r.diagnosed_cause == cause]
        c = [r for r in control if r.diagnosed_cause == cause]
        t_recovered = sum(1 for r in t if r.recovered_in_window)
        c_recovered = sum(1 for r in c if r.recovered_in_window)
        delta = rate(t_recovered, len(t)) - rate(c_recovered, len(c))
        mean_exposure = round(sum(r.exposure_paise for r in t) / len(t)) if t else 0

        lifts.append(
            CauseLift(
                cause=cause,
                treatment_cases=len(t),
                treatment_recovered=t_recovered,
                treatment_rate=rate(t_recovered, len(t)),
                holdout_cases=len(c),
                holdout_recovered=c_recovered,
                holdout_rate=rate(c_recovered, len(c)),
                incremental_rate=delta,
                incremental_paise=round(delta * len(t) * mean_exposure),
            )
        )
    return tuple(lifts)


def _channel_lifts(treated: list[CaseRecord], holdout_rate: float) -> tuple[ChannelLift, ...]:
    """A case touched on three channels appears in all three rows — these
    are overlapping views of the same cases, not a partition, and the row
    counts deliberately don't sum to the arm total."""
    channels = sorted({channel for r in treated for channel in r.channels_delivered})
    lifts = []
    for channel in channels:
        touched = [r for r in treated if channel in r.channels_delivered]
        recovered = sum(1 for r in touched if r.recovered_in_window)
        lifts.append(
            ChannelLift(
                channel=channel,
                cases_touched=len(touched),
                recovered=recovered,
                recovery_rate=rate(recovered, len(touched)),
                delta_vs_holdout=rate(recovered, len(touched)) - holdout_rate,
                cost_paise=sum(r.cost_paise for r in touched),
            )
        )
    return tuple(lifts)


def _diagnosis_accuracy(records: list[CaseRecord]) -> DiagnosisAccuracy | None:
    known = [r for r in records if r.true_cause is not None]
    if not known:
        return None
    correct = sum(1 for r in known if r.diagnosed_cause == r.true_cause)
    return DiagnosisAccuracy(cases_with_true_cause=len(known), correct=correct, accuracy=rate(correct, len(known)))


def _ground_truth_check(
    treated: list[CaseRecord], control: list[CaseRecord], incremental_rate: float, ci: ProportionCI | None
) -> GroundTruthCheck | None:
    treatment_causes = [r.true_cause for r in treated if r.true_cause is not None]
    control_causes = [r.true_cause for r in control if r.true_cause is not None]
    if not treatment_causes or not control_causes:
        return None
    return check_against_ground_truth(
        treatment_causes,
        control_causes,
        incremental_rate,
        ci.lower if ci else None,
        ci.upper if ci else None,
    )


def generate_batch_report(db: Session) -> BatchReport:
    """Convenience entry point: load from the database, then build."""
    records, unassigned = load_case_records(db)
    return build_report(records, unassigned_cases=unassigned, veto_reason_counts=load_veto_reason_counts(db))
