"""Pydantic schemas mirroring the 9 SQLAlchemy models.

Create schemas take only fields a caller can set; Read schemas add the
server-assigned id/timestamps and are built with from_attributes so they
can be constructed directly from ORM instances.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict


class CustomerBase(BaseModel):
    external_ref: str | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    dnd: bool = False
    whatsapp_opt_in: bool = False


class CustomerCreate(CustomerBase):
    pass


class CustomerRead(CustomerBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime


class SignalBase(BaseModel):
    customer_id: int
    lane: str
    source: str
    failure_code: str | None = None
    exposure_amount: int  # paise
    raw_payload: dict
    occurred_at: dt.datetime


class SignalCreate(SignalBase):
    pass


class SignalRead(SignalBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int | None = None
    received_at: dt.datetime


class CaseBase(BaseModel):
    customer_id: int
    lane: str
    exposure_amount: int  # paise
    detected_at: dt.datetime


class CaseCreate(CaseBase):
    true_root_cause: str | None = None


class CaseRead(CaseBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    state: str
    ladder_rung: int
    paused_until: dt.datetime | None = None
    true_root_cause: str | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class DiagnosisBase(BaseModel):
    case_id: int
    cause: str
    confidence: float = 1.0
    evidence: dict | None = None
    method: str


class DiagnosisCreate(DiagnosisBase):
    pass


class DiagnosisRead(DiagnosisBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime


class PlaybookBase(BaseModel):
    lane: str
    name: str
    version: str
    source_path: str
    content: dict
    is_active: bool = True


class PlaybookCreate(PlaybookBase):
    pass


class PlaybookRead(PlaybookBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    loaded_at: dt.datetime


class ActionBase(BaseModel):
    case_id: int
    playbook_id: int | None = None
    ladder_rung: int = 0
    proposed_action: str
    channel: str | None = None
    policy_verdict: str
    veto_reason_code: str | None = None
    executed_action: str | None = None
    cost: int = 0  # paise
    idempotency_key: str
    external_id: str | None = None
    llm_model: str | None = None
    llm_prompt_template_id: str | None = None
    llm_input_hash: str | None = None
    llm_raw_output: str | None = None
    llm_latency_ms: int | None = None


class ActionCreate(ActionBase):
    pass


class ActionRead(ActionBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    delivered: bool | None = None
    created_at: dt.datetime


class OutcomeBase(BaseModel):
    case_id: int
    action_id: int | None = None
    outcome_type: str
    amount: int | None = None  # paise
    promised_date: dt.datetime | None = None
    occurred_at: dt.datetime


class OutcomeCreate(OutcomeBase):
    pass


class OutcomeRead(OutcomeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: dt.datetime


class ExperimentAssignmentBase(BaseModel):
    case_id: int
    arm: str
    stratum: str
    seed: int


class ExperimentAssignmentCreate(ExperimentAssignmentBase):
    pass


class ExperimentAssignmentRead(ExperimentAssignmentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    assigned_at: dt.datetime


class CaseEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    case_id: int
    event_type: str
    payload: dict | None = None
    prev_hash: str
    hash: str
    created_at: dt.datetime
