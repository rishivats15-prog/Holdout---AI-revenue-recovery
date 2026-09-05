"""Assembles a case's replay timeline straight from `case_events`.

The ledger is the source of truth, not a convenience log: this module
walks it in order and renders what it finds. The one piece of joining it
does is pairing each `action_proposed` event with the `action_executed`
or `action_vetoed` event that answered it, so the page shows one node per
proposed action — the proposal, the policy verdict, and the outcome
together — rather than three disconnected rows.

Action rows are matched to their events positionally: execute_proposal
writes exactly one Action row and emits exactly one executed/vetoed event
per proposal, in the same order, so the Nth such event belongs to the Nth
action row for that case.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from db.enums import OutcomeType, PolicyVerdict
from db.models import Action, Case, CaseEvent, Customer, Diagnosis, ExperimentAssignment, Outcome, Signal
from decide.ladder import load_playbook
from policy import reasons

# Event types that carry an Action row alongside them.
ACTION_RESULT_EVENTS = ("action_executed", "action_vetoed")


@dataclass(frozen=True)
class CheckLine:
    label: str
    failed: bool


@dataclass(frozen=True)
class TimelineNode:
    key: str
    kind: str  # signal | diagnosis | action | gate | state
    title: str
    occurred_at: dt.datetime
    tone: str  # neutral | action | veto | recovered | paused
    summary: str = ""
    meta: tuple[tuple[str, str], ...] = ()
    reason_code: str | None = None
    reason_text: str | None = None
    checks: tuple[CheckLine, ...] = ()
    provenance: tuple[tuple[str, str], ...] = ()
    verdict_label: str | None = None
    delivered: bool | None = None


@dataclass
class CaseReplay:
    case: Case
    customer: Customer
    diagnosis: Diagnosis | None
    assignment: ExperimentAssignment | None
    signals: list[Signal]
    nodes: list[TimelineNode]
    events: list[CaseEvent]
    promise: Outcome | None
    payment: Outcome | None
    playbook_version: str
    ladder_length: int
    executed_count: int = 0
    vetoed_count: int = 0
    total_cost_paise: int = 0
    llm_diagnosed: bool = False


def build_replay(db: Session, case: Case) -> CaseReplay:
    customer = db.get(Customer, case.customer_id)
    diagnosis = (
        db.query(Diagnosis).filter(Diagnosis.case_id == case.id).order_by(Diagnosis.id.desc()).first()
    )
    assignment = db.query(ExperimentAssignment).filter(ExperimentAssignment.case_id == case.id).one_or_none()
    signals = db.query(Signal).filter(Signal.case_id == case.id).order_by(Signal.occurred_at).all()
    events = db.query(CaseEvent).filter(CaseEvent.case_id == case.id).order_by(CaseEvent.id).all()
    actions = db.query(Action).filter(Action.case_id == case.id).order_by(Action.id).all()
    outcomes = db.query(Outcome).filter(Outcome.case_id == case.id).order_by(Outcome.id).all()

    playbook = load_playbook(case.lane)
    ladder = playbook.ladders.get(diagnosis.cause, ()) if diagnosis else ()

    nodes = _build_nodes(events, actions, diagnosis)

    return CaseReplay(
        case=case,
        customer=customer,
        diagnosis=diagnosis,
        assignment=assignment,
        signals=signals,
        nodes=nodes,
        events=events,
        promise=next((o for o in reversed(outcomes) if o.outcome_type == OutcomeType.PTP.value), None),
        payment=next((o for o in outcomes if o.outcome_type == OutcomeType.PAYMENT.value), None),
        playbook_version=playbook.version,
        ladder_length=len(ladder),
        executed_count=sum(1 for a in actions if a.policy_verdict == PolicyVerdict.ALLOWED.value),
        vetoed_count=sum(1 for a in actions if a.policy_verdict == PolicyVerdict.VETOED.value),
        total_cost_paise=sum(a.cost for a in actions),
        llm_diagnosed=bool(diagnosis and diagnosis.method == "llm_fallback"),
    )


def _build_nodes(events: list[CaseEvent], actions: list[Action], diagnosis: Diagnosis | None) -> list[TimelineNode]:
    action_queue = list(actions)
    nodes: list[TimelineNode] = []
    pending_proposal: CaseEvent | None = None

    for event in events:
        payload = event.payload or {}

        if event.event_type == "action_proposed":
            pending_proposal = event
            continue

        if event.event_type in ACTION_RESULT_EVENTS:
            action = action_queue.pop(0) if action_queue else None
            nodes.append(_action_node(event, pending_proposal, action))
            pending_proposal = None
            continue

        builder = _SIMPLE_NODES.get(event.event_type)
        if builder is None:
            nodes.append(TimelineNode(
                key=f"e{event.id}", kind="state", title=event.event_type.replace("_", " ").capitalize(),
                occurred_at=event.created_at, tone="neutral",
            ))
            continue
        nodes.append(builder(event, payload, diagnosis))

    # A proposal with no result is a real, visible state: the case was
    # decided but the run ended before the gate ran. Never silently dropped.
    if pending_proposal is not None:
        payload = pending_proposal.payload or {}
        nodes.append(TimelineNode(
            key=f"e{pending_proposal.id}", kind="action", title="Action proposed, not yet evaluated",
            occurred_at=pending_proposal.created_at, tone="action",
            summary="The run ended before the policy gate evaluated this proposal.",
            meta=_proposal_meta(payload),
        ))

    return nodes


def _proposal_meta(payload: dict) -> tuple[tuple[str, str], ...]:
    return tuple(
        (label, str(value))
        for label, value in (
            ("Rung", payload.get("ladder_rung")),
            ("Action", payload.get("action_type")),
            ("Channel", payload.get("channel")),
        )
        if value is not None
    )


def _action_node(event: CaseEvent, proposal: CaseEvent | None, action: Action | None) -> TimelineNode:
    payload = event.payload or {}
    proposal_payload = (proposal.payload if proposal else None) or {}
    vetoed = event.event_type == "action_vetoed"

    rung = proposal_payload.get("ladder_rung", action.ladder_rung if action else None)
    action_type = payload.get("action_type") or (action.proposed_action if action else "action")
    channel = payload.get("channel") or (action.channel if action else None)

    meta = [
        ("Rung", str(rung) if rung is not None else "—"),
        ("Channel", (channel or "—").upper()),
        ("Action", action_type),
    ]
    if action is not None:
        meta.append(("Idempotency key", action.idempotency_key))
        if action.external_id:
            meta.append(("External id", action.external_id))
        if action.cost:
            meta.append(("Cost", f"{action.cost}p"))

    # Provenance is never ambiguous: an action selected from a playbook
    # rung says so and names the rung; a model-proposed one would carry its
    # logged model metadata here instead.
    provenance: list[tuple[str, str]] = [("Proposed by", f"rule · playbook rung {rung}")]
    if action is not None and action.llm_model:
        provenance = [
            ("Proposed by", "llm_fallback"),
            ("Model", action.llm_model),
            ("Prompt template", action.llm_prompt_template_id or "—"),
            ("Input hash", (action.llm_input_hash or "")[:16]),
            ("Latency", f"{action.llm_latency_ms}ms" if action.llm_latency_ms is not None else "—"),
        ]

    if vetoed:
        reason_code = payload.get("reason_code") or (action.veto_reason_code if action else None)
        evaluated = tuple(payload.get("checks_evaluated") or ())
        checks = tuple(
            CheckLine(label=reasons.CHECK_LABELS.get(name, name), failed=(index == len(evaluated) - 1))
            for index, name in enumerate(evaluated)
        )
        return TimelineNode(
            key=f"e{event.id}", kind="gate", title=f"{_humanize(action_type)} blocked at the policy gate",
            occurred_at=event.created_at, tone="veto",
            meta=tuple(meta), reason_code=reason_code, reason_text=reasons.describe(reason_code),
            checks=checks, provenance=tuple(provenance), verdict_label="VETOED",
        )

    delivered = payload.get("delivery_success")
    return TimelineNode(
        key=f"e{event.id}", kind="action", title=_humanize(action_type),
        occurred_at=event.created_at, tone="action",
        summary="Delivered." if delivered else "Sent, but the channel did not confirm delivery.",
        meta=tuple(meta), provenance=tuple(provenance), verdict_label="ALLOWED", delivered=bool(delivered),
    )


def _humanize(action_type: str) -> str:
    return action_type.replace("_", " ").capitalize()


# --- simple, single-event nodes ----------------------------------------


def _node(kind: str, tone: str, title_fn, summary_fn=None, meta_fn=None):
    def build(event: CaseEvent, payload: dict, diagnosis: Diagnosis | None) -> TimelineNode:
        return TimelineNode(
            key=f"e{event.id}", kind=kind, title=title_fn(payload), occurred_at=event.created_at, tone=tone,
            summary=summary_fn(payload) if summary_fn else "",
            meta=meta_fn(payload, diagnosis) if meta_fn else (),
            provenance=_diagnosis_provenance(diagnosis) if kind == "diagnosis" else (),
        )

    return build


def _diagnosis_provenance(diagnosis: Diagnosis | None) -> tuple[tuple[str, str], ...]:
    if diagnosis is None:
        return ()
    evidence = diagnosis.evidence or {}
    if diagnosis.method == "llm_fallback":
        return (
            ("Method", "llm_fallback"),
            ("Model", str(evidence.get("model", "—"))),
            ("Prompt template", str(evidence.get("prompt_template_id", "—"))),
            ("Input hash", str(evidence.get("input_hash", ""))[:16]),
            ("Raw output", str(evidence.get("raw_output", "—"))),
            ("Latency", f"{evidence.get('latency_ms', '—')}ms"),
        )
    return (
        ("Method", "rule"),
        ("Failure code", str(evidence.get("failure_code", "—"))),
        ("Retry viable", str(evidence.get("retry_viable", "—"))),
    )


_SIMPLE_NODES = {
    "case_detected": _node(
        "signal", "neutral",
        lambda p: "Case opened",
        lambda p: "First failure signal for this customer in this lane — no open case existed, so one was created.",
        lambda p, d: (("Signal", f"#{p.get('signal_id')}"), ("Source", str(p.get("source", "—")))),
    ),
    "signal_folded": _node(
        "signal", "neutral",
        lambda p: "Repeat failure folded in",
        lambda p: "A further retry failed for the same customer. Deduplicated into this case rather than opening a new one.",
        lambda p, d: (("Signal", f"#{p.get('signal_id')}"), ("Source", str(p.get("source", "—")))),
    ),
    "case_diagnosed": _node(
        "diagnosis", "neutral",
        lambda p: f"Diagnosed · {p.get('cause', 'unknown')}",
        lambda p: "",
        lambda p, d: ((("Confidence"), f"{float(p.get('confidence', 0)):.2f}"),),
    ),
    "case_routed": _node(
        "state", "neutral",
        lambda p: f"Routed to {str(p.get('terminal_state', '')).replace('_', ' ')}",
        lambda p: "The playbook's ladder for this cause routes the case out of automated treatment at this rung.",
        lambda p, d: (("Action", str(p.get("action_type", "—"))), ("Cause", str(p.get("cause", "—")))),
    ),
    "case_paused": _node(
        "state", "paused",
        lambda p: "Promise to pay — case paused",
        lambda p: "The customer committed to a date. Treatment freezes until that date plus the playbook's grace window.",
        lambda p, d: (("Promised", str(p.get("promised_date", "—"))[:16]), ("Paused until", str(p.get("paused_until", "—"))[:16])),
    ),
    "promise_broken": _node(
        "state", "paused",
        lambda p: "Promise broken — re-entered treatment",
        lambda p: "Payment had not landed by the grace deadline, so the case resumes one rung higher rather than starting over.",
        lambda p, d: ((("New rung"), str(p.get("new_ladder_rung", "—"))),),
    ),
    "rung_skipped": _node(
        "state", "veto",
        lambda p: "Rung skipped",
        lambda p: "The same rung was vetoed repeatedly for a case-specific reason, so the ladder moves past it rather than looping.",
        lambda p, d: (("Rung", str(p.get("ladder_rung", "—"))), ("Reason", str(p.get("reason", "—"))), ("Attempts", str(p.get("attempts", "—")))),
    ),
    "case_recovered": _node(
        "state", "recovered",
        lambda p: "Recovered — payment received",
        lambda p: "",
        lambda p, d: (("Arm", str(p.get("arm", "—"))), ("Day", str(p.get("recovery_day", "—"))), ("Prior state", str(p.get("prior_state", "—")))),
    ),
    "case_written_off": _node(
        "state", "neutral",
        lambda p: "Written off",
        lambda p: "Further treatment would cost more than it could expect to recover.",
        lambda p, d: ((("Reason"), str(p.get("reason", "—"))),),
    ),
    "case_suppressed": _node(
        "state", "veto",
        lambda p: "Suppressed",
        lambda p: "All further contact stopped for this case.",
        lambda p, d: ((("Reason"), str(p.get("reason", "—"))),),
    ),
    "case_disputed": _node(
        "state", "veto",
        lambda p: "Complaint raised — routed to human queue",
        lambda p: "The customer disputed. Automated dunning stops here; a person takes it from this point.",
        lambda p, d: ((("Action"), f"#{p.get('action_id')}"),),
    ),
}
