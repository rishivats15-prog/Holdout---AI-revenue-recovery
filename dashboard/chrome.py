"""Shared page chrome: the rail's nav and live indicators, and the status
bar every route stamps across the top of its content.

The status bar's contents are contextual per route — the batch number and
window on the scoreboard, the case and rung on a replay — but the
synthetic marker and the clock are on every page without exception. A
judge should never be more than one glance from knowing that this data is
synthetic and that its ground truth is known.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy.orm import Session

from db.enums import PolicyVerdict as VerdictEnum
from db.models import Action
from policy import killswitch, reasons
from policy.context import CircuitBreaker
from policy.gate import POLICY_VERSION
from sim.config import load_ground_truth

SYNTHETIC_MARK = "SYNTHETIC · GROUND TRUTH KNOWN"

NAV = (
    ("/", "Scoreboard"),
    ("/run", "Live run"),
    ("/cases", "Cases"),
    ("/vetoes", "Veto log"),
    ("/playbooks", "Playbooks"),
)


@dataclass(frozen=True)
class NavItem:
    href: str
    label: str
    active: bool


@dataclass(frozen=True)
class StatusItem:
    text: str
    mark: bool = False


@dataclass(frozen=True)
class BreakerState:
    """The rail's live circuit-breaker indicator.

    The breaker itself is per-run, in-process state (policy/context.py), so
    what's shown here is the trace it left in the database: whether any
    action in the batch was actually vetoed with CIRCUIT_BREAKER_OPEN, and
    the batch's delivery-failure rate against the threshold that would
    trip it. That is a fact about what happened, not a guess about a
    process that is no longer running.
    """

    tripped: bool
    failure_rate: float
    threshold: float
    attempts: int

    @property
    def label(self) -> str:
        if self.tripped:
            return "TRIPPED"
        if self.attempts == 0:
            return "NO DATA"
        return f"CLOSED {self.failure_rate * 100:.1f}%"


def nav_items(active_href: str) -> list[NavItem]:
    return [
        NavItem(href=href, label=label, active=(href == active_href or (href != "/" and active_href.startswith(href))))
        for href, label in NAV
    ]


def breaker_state(db: Session) -> BreakerState:
    defaults = CircuitBreaker()
    executed = db.query(Action).filter(Action.policy_verdict == VerdictEnum.ALLOWED.value).all()
    attempts = len(executed)
    failures = sum(1 for action in executed if action.delivered is False)
    tripped = (
        db.query(Action).filter(Action.veto_reason_code == reasons.CIRCUIT_BREAKER_OPEN).count() > 0
    )
    return BreakerState(
        tripped=tripped,
        failure_rate=(failures / attempts) if attempts else 0.0,
        threshold=defaults.failure_threshold,
        attempts=attempts,
    )


def batch_label() -> str:
    """A stable, honest batch number: the last four digits of the seed the
    synthetic world was generated from. Same seed in, same batch number
    out — there is no batches table to draw a real id from, and inventing
    an incrementing one would imply a history that doesn't exist."""
    return f"{load_ground_truth().seed % 10000:04d}"


def base_context(db: Session, *, active_href: str, page_title: str, status_items: list[StatusItem]) -> dict:
    return {
        "page_title": page_title,
        "nav": nav_items(active_href),
        "breaker": breaker_state(db),
        "kill_switch": killswitch.read_status(),
        "status_bar": status_items + [StatusItem(SYNTHETIC_MARK, mark=True)],
        "clock": dt.datetime.now().strftime("%H:%M"),
        "policy_version": POLICY_VERSION,
    }


def scoreboard_status(report) -> list[StatusItem]:
    total = report.treatment.cases + report.holdout.cases
    treated_pct = round(report.treatment.cases / total * 100) if total else 0
    return [
        StatusItem(f"N° BATCH / {batch_label()}"),
        StatusItem(f"WINDOW {report.attribution_window_days}D"),
        StatusItem(f"ARM SPLIT {treated_pct}/{100 - treated_pct}"),
        StatusItem(f"POLICY v{POLICY_VERSION}"),
    ]


def case_status(case, rung_label: str, playbook_version: str) -> list[StatusItem]:
    return [
        StatusItem(f"CASE / {case.id:04d}"),
        StatusItem(f"LANE {case.lane.upper()}"),
        StatusItem(f"RUNG {rung_label}"),
        StatusItem(f"PLAYBOOK v{playbook_version}"),
        StatusItem(f"POLICY v{POLICY_VERSION}"),
    ]


def veto_status(total_vetoes: int, distinct_codes: int, filtered: str | None) -> list[StatusItem]:
    items = [
        StatusItem(f"VETOES {total_vetoes}"),
        StatusItem(f"CODES {distinct_codes}"),
        StatusItem(f"POLICY v{POLICY_VERSION}"),
    ]
    if filtered:
        items.insert(1, StatusItem(f"FILTER {filtered}"))
    return items


def run_status(session, total_cases: int) -> list[StatusItem]:
    if session is None:
        # Data can exist without a live session — `make seed` runs all 21
        # days in its own process. Saying "not started" there would be
        # wrong; there is simply no per-day run to step.
        return [
            StatusItem(f"N° BATCH / {batch_label()}"),
            StatusItem("NO LIVE RUN" if total_cases else "RUN NOT STARTED"),
            StatusItem(f"CASES {total_cases}"),
            StatusItem(f"POLICY v{POLICY_VERSION}"),
        ]
    return [
        StatusItem(f"N° BATCH / {batch_label()}"),
        StatusItem(f"DAY {session.tick_number} / {session.total_ticks}"),
        StatusItem(f"CASES {total_cases}"),
        StatusItem("BREAKER OPEN" if session.circuit_breaker.is_open() else "BREAKER CLOSED"),
        StatusItem(f"POLICY v{POLICY_VERSION}"),
    ]


def queue_status(shown: int, total: int, applied: str | None) -> list[StatusItem]:
    items = [
        StatusItem(f"N° BATCH / {batch_label()}"),
        StatusItem(f"SHOWING {shown} / {total}"),
        StatusItem(f"POLICY v{POLICY_VERSION}"),
    ]
    if applied:
        items.insert(2, StatusItem(f"FILTER {applied}"))
    return items
