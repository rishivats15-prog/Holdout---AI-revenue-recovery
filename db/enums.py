"""Fixed value sets shared by SQLAlchemy models and Pydantic schemas.

These are data, not decision logic: they declare which values are legal, they
do not decide which one applies to a given case. The signal -> root cause
mapping itself lives in /diagnosis and /playbooks, not here.
"""

import enum


class Lane(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    B2B = "b2b"


class SignalSource(str, enum.Enum):
    PAYMENT_WEBHOOK = "payment_webhook"
    ABANDONED_CART = "abandoned_cart"
    OVERDUE_INVOICE = "overdue_invoice"


class CaseState(str, enum.Enum):
    DETECTED = "detected"
    DIAGNOSED = "diagnosed"
    IN_TREATMENT = "in_treatment"
    PAUSED = "paused"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"
    HUMAN_QUEUE = "human_queue"
    SUPPRESSED = "suppressed"


class RootCause(str, enum.Enum):
    # subscription lane
    BALANCE_TIMING = "balance_timing"
    STALE_CREDENTIAL = "stale_credential"
    ISSUER_SOFT_DECLINE = "issuer_soft_decline"
    MANDATE_DEAD = "mandate_dead"
    MANDATE_CAP_EXCEEDED = "mandate_cap_exceeded"
    GATEWAY_TIMEOUT = "gateway_timeout"
    RISK_FRAUD = "risk_fraud"
    DEAD_INSTRUMENT = "dead_instrument"
    # b2b lane
    PO_GRN_MISMATCH = "po_grn_mismatch"
    GOODS_DISPUTE = "goods_dispute"
    WRONG_AP_CONTACT = "wrong_ap_contact"
    CASH_FLOW_STRESS = "cash_flow_stress"


class DiagnosisMethod(str, enum.Enum):
    RULE = "rule"
    LLM_FALLBACK = "llm_fallback"


class PolicyVerdict(str, enum.Enum):
    ALLOWED = "allowed"
    VETOED = "vetoed"


class OutcomeType(str, enum.Enum):
    PAYMENT = "payment"
    PTP = "ptp"
    DISPUTE = "dispute"


class ExperimentArm(str, enum.Enum):
    TREATMENT = "treatment"
    HOLDOUT = "holdout"
