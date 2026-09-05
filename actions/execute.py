"""Execute step — the policy gate's other half. For each proposed action:
evaluate the gate, then either write a vetoed Action row (no send, no
cost) or call the right channel adapter and write an executed Action row.

Idempotency is real, not decorative: the key is deterministic from
(case, playbook, rung, tick), so re-running execute for the exact same
attempt within the same tick returns the existing row instead of sending
twice — while a genuine retry of the same rung on a *later* tick (e.g.
after a transient veto like quiet hours clears) still gets a fresh key and
is correctly re-evaluated rather than silently short-circuited.
"""

from __future__ import annotations

import datetime as dt
import random

from sqlalchemy.orm import Session

from actions import email, retry, sms, voice, whatsapp
from db.hashchain import append_case_event
from db.models import Action, Case, Customer, utcnow
from decide.decide import ProposedAction
from policy.context import CircuitBreaker, PolicyContext
from policy.gate import evaluate_policy

EXECUTION_RNG_SEED = 424242  # independent of sim's seed — execute doesn't know or care where proposals came from

CHANNEL_ADAPTERS = {
    "retry": retry.send,
    "sms": sms.send,
    "whatsapp": whatsapp.send,
    "voice": voice.send,
    "email": email.send,
}


def build_idempotency_key(case_id: int, playbook_id: int, ladder_rung: int, tick_number: int = 0) -> str:
    return f"case:{case_id}:playbook:{playbook_id}:rung:{ladder_rung}:tick:{tick_number}"


def execute_proposal(
    db: Session,
    case: Case,
    customer: Customer,
    proposal: ProposedAction,
    circuit_breaker: CircuitBreaker,
    rng: random.Random,
    now: dt.datetime,
    tick_number: int = 0,
) -> Action:
    if proposal.channel is None:
        raise ValueError(
            f"execute_proposal received a channel-less proposal for case {case.id} "
            "(terminal/routing rungs already took full effect inside decide_case — "
            "the caller should filter proposals to proposal.channel is not None "
            "before handing them to Execute)"
        )

    idempotency_key = build_idempotency_key(case.id, proposal.playbook_id, proposal.ladder_rung, tick_number)
    existing = db.query(Action).filter(Action.idempotency_key == idempotency_key).one_or_none()
    if existing is not None:
        return existing

    prior_actions = db.query(Action).filter(Action.case_id == case.id).all()
    ctx = PolicyContext(
        case=case, customer=customer, proposal=proposal, prior_actions=prior_actions, now=now, circuit_breaker=circuit_breaker
    )
    verdict = evaluate_policy(ctx)

    if not verdict.allowed:
        action = Action(
            case_id=case.id,
            playbook_id=proposal.playbook_id,
            ladder_rung=proposal.ladder_rung,
            proposed_action=proposal.action_type,
            channel=proposal.channel,
            policy_verdict="vetoed",
            veto_reason_code=verdict.reason_code,
            executed_action=None,
            cost=0,
            delivered=None,
            idempotency_key=idempotency_key,
        )
        db.add(action)
        db.flush()
        append_case_event(
            db,
            case.id,
            "action_vetoed",
            {
                "action_type": proposal.action_type,
                "channel": proposal.channel,
                "reason_code": verdict.reason_code,
                "checks_evaluated": list(verdict.checks_evaluated),
            },
        )
        return action

    adapter = CHANNEL_ADAPTERS[proposal.channel]
    result = adapter(case, customer, proposal, rng)
    circuit_breaker.record_outcome(result.success)

    action = Action(
        case_id=case.id,
        playbook_id=proposal.playbook_id,
        ladder_rung=proposal.ladder_rung,
        proposed_action=proposal.action_type,
        channel=proposal.channel,
        policy_verdict="allowed",
        veto_reason_code=None,
        executed_action=proposal.action_type,
        cost=proposal.cost_paise,
        delivered=result.success,
        idempotency_key=idempotency_key,
        external_id=result.external_id,
    )
    db.add(action)
    case.ladder_rung = proposal.ladder_rung + 1
    db.flush()
    append_case_event(
        db,
        case.id,
        "action_executed",
        {
            "action_type": proposal.action_type,
            "channel": proposal.channel,
            "external_id": result.external_id,
            "delivery_success": result.success,
        },
    )
    return action


def run_batch(
    db: Session,
    proposals: list[ProposedAction],
    now: dt.datetime | None = None,
    tick_number: int = 0,
    circuit_breaker: CircuitBreaker | None = None,
) -> dict:
    """Gates and executes a batch of proposals. Proposals from a terminal
    (channel-less) rung are skipped here, not vetoed — they already took
    full effect in decide_case (the case was routed to its terminal state
    directly), so there's nothing left for Execute to do.

    Pass a shared `circuit_breaker` across ticks of the same orchestrator
    run so a batch-wide failure spike stays tripped for the rest of the
    run, not just the tick that caused it; omitting it makes one fresh for
    this call only (the old Phase 4 single-pass behaviour).
    """
    now = now or utcnow().replace(hour=12, minute=0, second=0, microsecond=0)
    rng = random.Random(EXECUTION_RNG_SEED + tick_number)
    circuit_breaker = circuit_breaker if circuit_breaker is not None else CircuitBreaker()

    channel_proposals = [p for p in proposals if p.channel is not None]
    routed = len(proposals) - len(channel_proposals)

    allowed = 0
    vetoed = 0
    actions: list[Action] = []
    for proposal in channel_proposals:
        case = db.get(Case, proposal.case_id)
        customer = db.get(Customer, case.customer_id)
        action = execute_proposal(db, case, customer, proposal, circuit_breaker, rng, now, tick_number)
        actions.append(action)
        if action.policy_verdict == "allowed":
            allowed += 1
        else:
            vetoed += 1

    db.commit()
    return {
        "allowed": allowed,
        "vetoed": vetoed,
        "routed": routed,
        "circuit_breaker_tripped": circuit_breaker.is_open(),
        "actions": actions,
    }
