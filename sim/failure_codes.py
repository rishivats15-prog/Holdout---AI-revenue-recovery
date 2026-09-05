"""Realistic gateway-style failure codes per root cause, and the free-text
messages used for the fraction of episodes that stay ambiguous (exercised
by the Phase 2 LLM-fallback path).

This mapping is also the answer key the Phase 2 diagnosis rules table has
to reproduce — a clean failure_code always corresponds to exactly one cause.
"""

FAILURE_CODES = {
    "balance_timing": ["INSUFFICIENT_FUNDS"],
    "stale_credential": ["CARD_EXPIRED", "INVALID_CARD"],
    "issuer_soft_decline": ["DO_NOT_HONOR", "ISSUER_SOFT_DECLINE"],
    "mandate_dead": ["MANDATE_NOT_FOUND", "MANDATE_REVOKED"],
    "mandate_cap_exceeded": ["MANDATE_LIMIT_EXCEEDED"],
    "gateway_timeout": ["GATEWAY_TIMEOUT"],
    "risk_fraud": ["RISK_FRAUD_SUSPECTED"],
    "dead_instrument": ["ACCOUNT_CLOSED", "INSTRUMENT_FROZEN"],
}

AMBIGUOUS_MESSAGES = [
    "Payment could not be processed. Please contact your bank for details.",
    "Transaction declined by issuer. No further details provided.",
    "Charge unsuccessful — reason not specified by acquirer.",
    "Payment failed. Support ticket #{ticket} raised for manual review.",
]
