"""The ten policy checks — one per bound in CLAUDE.md's policy gate list,
plus the kill switch. Each check is a plain function: PolicyContext in, a
reason code out if it vetoes, None if it passes. No check ever calls a
model; every one is a deterministic, independently testable function of
its inputs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from decide.ladder import load_playbook
from policy import reasons
from policy.context import PolicyContext
from policy.killswitch import KILL_SWITCH_PATH, is_engaged

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_DIR = Path(__file__).resolve().parent

HUMAN_FACING_CHANNELS = frozenset({"sms", "whatsapp", "voice"})
QUIET_HOURS_START_HOUR = 8   # 8am
QUIET_HOURS_END_HOUR = 19    # 7pm, exclusive

MAX_TOUCHES_PER_CHANNEL_PER_DAY = 1
MAX_WAIVER_PAISE_WITHOUT_SIGNOFF = 50_000  # ₹500
COST_CEILING_PCT_OF_EXPOSURE = 0.08         # 8%

BANNED_PHRASES = (
    "legal action",
    "lawsuit",
    "we will sue",
    "sue you",
    "inform your employer",
    "share your details with",
    "police complaint",
    "fir against",
)


# --- data loaders -----------------------------------------------------


@dataclass(frozen=True)
class RecoveryPriors:
    lane: str
    rung_decay_factor: float
    base_recovery_probability: dict[str, float]


@lru_cache(maxsize=None)
def _load_recovery_priors(lane: str) -> RecoveryPriors:
    raw = yaml.safe_load((POLICY_DIR / "recovery_priors.yaml").read_text())
    if raw["lane"] != lane:
        raise ValueError(f"recovery_priors.yaml is for lane {raw['lane']!r}, not {lane!r}")
    return RecoveryPriors(
        lane=raw["lane"],
        rung_decay_factor=raw["rung_decay_factor"],
        base_recovery_probability=raw["base_recovery_probability"],
    )


@lru_cache(maxsize=None)
def _load_dlt_approved_templates(lane: str) -> frozenset[str]:
    raw = yaml.safe_load((POLICY_DIR / "dlt_templates.yaml").read_text())
    if raw["lane"] != lane:
        raise ValueError(f"dlt_templates.yaml is for lane {raw['lane']!r}, not {lane!r}")
    return frozenset(raw["approved_template_ids"])


def _cooldown_for_rung(lane: str, cause: str, ladder_rung: int) -> int:
    ladder = load_playbook(lane).ladders[cause]
    if ladder_rung < len(ladder):
        return ladder[ladder_rung].cooldown_minutes
    return 0


# --- checks -------------------------------------------------------------


def check_kill_switch(ctx: PolicyContext) -> str | None:
    """Reads the flag through policy/killswitch.py — the same call the
    dashboard rail and the CLI make, so the gate can never be halted while
    the UI says running, or the reverse."""
    if is_engaged(KILL_SWITCH_PATH):
        return reasons.KILL_SWITCH_ENGAGED
    return None


def check_circuit_breaker(ctx: PolicyContext) -> str | None:
    if ctx.circuit_breaker.is_open():
        return reasons.CIRCUIT_BREAKER_OPEN
    return None


def check_action_allowlist(ctx: PolicyContext) -> str | None:
    """The proposal must be exactly the rung the playbook declares for this
    cause and rung index — action_type, channel, and every playbook-sourced
    parameter (cost, template, waiver). Defense in depth against a buggy or
    malicious proposal that didn't actually come from decide_case: it's not
    enough to only check the action's name, since a proposal could reuse a
    legitimate action_type/channel pair while inflating its own cost or
    waiver — "cannot invent one" has to cover the parameters too.
    """
    ladder = load_playbook(ctx.case.lane).ladders.get(ctx.proposal.cause)
    if ladder is None or ctx.proposal.ladder_rung >= len(ladder):
        return reasons.ACTION_NOT_IN_PLAYBOOK
    rung = ladder[ctx.proposal.ladder_rung]
    matches = (
        rung.action_type == ctx.proposal.action_type
        and rung.channel == ctx.proposal.channel
        and rung.terminal_state == ctx.proposal.terminal_state
        and rung.cost_paise == ctx.proposal.cost_paise
        and rung.copy_template == ctx.proposal.copy_template
        and rung.waiver_offer_paise == ctx.proposal.waiver_offer_paise
    )
    if not matches:
        return reasons.ACTION_NOT_IN_PLAYBOOK
    return None


def check_quiet_hours(ctx: PolicyContext) -> str | None:
    if ctx.proposal.channel not in HUMAN_FACING_CHANNELS:
        return None
    hour = ctx.now.hour
    if not (QUIET_HOURS_START_HOUR <= hour < QUIET_HOURS_END_HOUR):
        return reasons.OUTSIDE_QUIET_HOURS
    return None


def check_frequency_caps(ctx: PolicyContext) -> str | None:
    if ctx.proposal.channel is None:
        return None  # a terminal/no-channel rung isn't a "touch"

    allowed_prior = [a for a in ctx.prior_actions if a.policy_verdict == "allowed"]

    touches_today = [
        a for a in allowed_prior if a.channel == ctx.proposal.channel and a.created_at.date() == ctx.now.date()
    ]
    if len(touches_today) >= MAX_TOUCHES_PER_CHANNEL_PER_DAY:
        return reasons.CHANNEL_FREQUENCY_CAP_EXCEEDED

    if allowed_prior:
        last_action = max(allowed_prior, key=lambda a: a.created_at)
        cooldown = _cooldown_for_rung(ctx.case.lane, ctx.proposal.cause, last_action.ladder_rung)
        elapsed_minutes = (ctx.now - last_action.created_at).total_seconds() / 60
        if elapsed_minutes < cooldown:
            return reasons.RUNG_COOLDOWN_ACTIVE

    return None


def check_consent_and_eligibility(ctx: PolicyContext) -> str | None:
    channel = ctx.proposal.channel
    if channel in HUMAN_FACING_CHANNELS and ctx.customer.dnd:
        return reasons.CUSTOMER_DND
    if channel == "whatsapp" and not ctx.customer.whatsapp_opt_in:
        return reasons.WHATSAPP_NOT_OPTED_IN
    if channel == "sms":
        approved = _load_dlt_approved_templates(ctx.case.lane)
        if ctx.proposal.copy_template not in approved:
            return reasons.SMS_TEMPLATE_NOT_DLT_APPROVED
    return None


def check_financial_authority(ctx: PolicyContext) -> str | None:
    if ctx.proposal.waiver_offer_paise > MAX_WAIVER_PAISE_WITHOUT_SIGNOFF:
        return reasons.FINANCIAL_AUTHORITY_EXCEEDED
    return None


def check_cost_ceiling(ctx: PolicyContext) -> str | None:
    spent_so_far = sum(a.cost for a in ctx.prior_actions if a.policy_verdict == "allowed")
    projected_total = spent_so_far + ctx.proposal.cost_paise
    ceiling = ctx.case.exposure_amount * COST_CEILING_PCT_OF_EXPOSURE
    if projected_total > ceiling:
        return reasons.COST_CEILING_EXCEEDED
    return None


def check_expected_value_floor(ctx: PolicyContext) -> str | None:
    priors = _load_recovery_priors(ctx.case.lane)
    base_p = priors.base_recovery_probability[ctx.proposal.cause]
    p_this_touch = base_p * (priors.rung_decay_factor ** ctx.proposal.ladder_rung)
    expected_value = p_this_touch * ctx.case.exposure_amount
    if expected_value < ctx.proposal.cost_paise:
        return reasons.EXPECTED_VALUE_BELOW_COST
    return None


def check_content_rules(ctx: PolicyContext) -> str | None:
    haystack = " ".join(filter(None, [ctx.proposal.copy_template, ctx.proposal.copy_text])).lower()
    for phrase in BANNED_PHRASES:
        if phrase in haystack:
            return reasons.CONTENT_POLICY_VIOLATION
    return None
