"""Veto reason codes. Uppercase constants — the dashboard's veto log and
case replay page render these verbatim (per CLAUDE.md's Dashboard design,
reason codes are uppercase mono, never prose).
"""

KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
CIRCUIT_BREAKER_OPEN = "CIRCUIT_BREAKER_OPEN"
ACTION_NOT_IN_PLAYBOOK = "ACTION_NOT_IN_PLAYBOOK"
OUTSIDE_QUIET_HOURS = "OUTSIDE_QUIET_HOURS"
CHANNEL_FREQUENCY_CAP_EXCEEDED = "CHANNEL_FREQUENCY_CAP_EXCEEDED"
RUNG_COOLDOWN_ACTIVE = "RUNG_COOLDOWN_ACTIVE"
CUSTOMER_DND = "CUSTOMER_DND"
WHATSAPP_NOT_OPTED_IN = "WHATSAPP_NOT_OPTED_IN"
SMS_TEMPLATE_NOT_DLT_APPROVED = "SMS_TEMPLATE_NOT_DLT_APPROVED"
FINANCIAL_AUTHORITY_EXCEEDED = "FINANCIAL_AUTHORITY_EXCEEDED"
COST_CEILING_EXCEEDED = "COST_CEILING_EXCEEDED"
EXPECTED_VALUE_BELOW_COST = "EXPECTED_VALUE_BELOW_COST"
CONTENT_POLICY_VIOLATION = "CONTENT_POLICY_VIOLATION"

ALL_REASON_CODES = frozenset(
    {
        KILL_SWITCH_ENGAGED,
        CIRCUIT_BREAKER_OPEN,
        ACTION_NOT_IN_PLAYBOOK,
        OUTSIDE_QUIET_HOURS,
        CHANNEL_FREQUENCY_CAP_EXCEEDED,
        RUNG_COOLDOWN_ACTIVE,
        CUSTOMER_DND,
        WHATSAPP_NOT_OPTED_IN,
        SMS_TEMPLATE_NOT_DLT_APPROVED,
        FINANCIAL_AUTHORITY_EXCEEDED,
        COST_CEILING_EXCEEDED,
        EXPECTED_VALUE_BELOW_COST,
        CONTENT_POLICY_VIOLATION,
    }
)


# Plain-English gloss for each code. Copy, not logic — nothing branches on
# these strings. They exist so a veto is legible on the dashboard to
# someone who has never read the policy module, which is the whole point
# of logging a reason code rather than just a boolean.
DESCRIPTIONS = {
    KILL_SWITCH_ENGAGED: "An operator has halted all outbound action.",
    CIRCUIT_BREAKER_OPEN: "Delivery failures spiked across the batch, so outbound action stopped automatically.",
    ACTION_NOT_IN_PLAYBOOK: "The proposed action is not declared in this lane's playbook.",
    OUTSIDE_QUIET_HOURS: "The send fell outside the 8am–7pm contact window.",
    CHANNEL_FREQUENCY_CAP_EXCEEDED: "This customer had already been contacted on this channel today.",
    RUNG_COOLDOWN_ACTIVE: "The cooldown after the previous rung had not elapsed yet.",
    CUSTOMER_DND: "The customer is on the do-not-disturb list.",
    WHATSAPP_NOT_OPTED_IN: "The customer has not opted in to WhatsApp.",
    SMS_TEMPLATE_NOT_DLT_APPROVED: "The SMS template is not registered under DLT.",
    FINANCIAL_AUTHORITY_EXCEEDED: "The waiver offered exceeds what may be approved without human sign-off.",
    COST_CEILING_EXCEEDED: "Spend on this case has reached its cap as a share of the exposure.",
    EXPECTED_VALUE_BELOW_COST: "The next touch costs more than the recovery it can expect to produce.",
    CONTENT_POLICY_VIOLATION: "The message copy contained a prohibited phrase.",
}

# The gate's check ids, in the order policy/gate.py runs them, with the
# bound each one enforces. Used by the case replay to show every rule that
# was evaluated before a veto fired, not just the one that fired.
CHECK_LABELS = {
    "kill_switch": "Kill switch",
    "circuit_breaker": "Circuit breaker",
    "action_allowlist": "Action allowlist",
    "quiet_hours": "Quiet hours",
    "frequency_caps": "Frequency caps",
    "consent_and_eligibility": "Consent and channel eligibility",
    "financial_authority": "Financial authority",
    "cost_ceiling": "Cost ceiling",
    "expected_value_floor": "Expected-value floor",
    "content_rules": "Content rules",
}


def describe(reason_code: str | None) -> str:
    return DESCRIPTIONS.get(reason_code or "", "No description recorded for this reason code.")


# Which reason codes each gate check can emit, in the order gate.py runs
# them. The veto log uses this to show every bound and how often it fired
# — including the ones that fired zero times. A gate is only credible if
# you can see the whole of it, not just the parts a given batch happened
# to trip.
CHECK_REASONS = {
    "kill_switch": (KILL_SWITCH_ENGAGED,),
    "circuit_breaker": (CIRCUIT_BREAKER_OPEN,),
    "action_allowlist": (ACTION_NOT_IN_PLAYBOOK,),
    "quiet_hours": (OUTSIDE_QUIET_HOURS,),
    "frequency_caps": (CHANNEL_FREQUENCY_CAP_EXCEEDED, RUNG_COOLDOWN_ACTIVE),
    "consent_and_eligibility": (CUSTOMER_DND, WHATSAPP_NOT_OPTED_IN, SMS_TEMPLATE_NOT_DLT_APPROVED),
    "financial_authority": (FINANCIAL_AUTHORITY_EXCEEDED,),
    "cost_ceiling": (COST_CEILING_EXCEEDED,),
    "expected_value_floor": (EXPECTED_VALUE_BELOW_COST,),
    "content_rules": (CONTENT_POLICY_VIOLATION,),
}

# The bound each check enforces, stated as the rule rather than the code.
CHECK_BOUNDS = {
    "kill_switch": "An operator can halt every outbound action with one command.",
    "circuit_breaker": "A batch-wide delivery-failure spike halts outbound action automatically.",
    "action_allowlist": "Actions come from the lane's playbook; the agent cannot invent one.",
    "quiet_hours": "No contact outside 8am–7pm.",
    "frequency_caps": "One touch per channel per day, plus a cooldown between escalation rungs.",
    "consent_and_eligibility": "DND, WhatsApp opt-in, and DLT template registration are all enforced.",
    "financial_authority": "Waivers above ₹500 need human sign-off.",
    "cost_ceiling": "Cumulative spend on a case is capped at 8% of its exposure.",
    "expected_value_floor": "Stop when p(recover) × exposure falls below the next touch's cost.",
    "content_rules": "No legal threats and no third-party disclosure in outbound copy.",
}
