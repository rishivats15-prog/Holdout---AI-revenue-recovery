"""SQLAlchemy models for the 9 core tables.

Lane/state/cause columns are plain strings, not DB-level enums, on purpose:
the diagnosis and playbook tables are meant to be extended by adding YAML,
not by migrating the schema. db/enums.py documents the legal values for
validation at the Pydantic/application layer instead.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow() -> dt.datetime:
    """Naive UTC — deliberately, not tz-aware. SQLite has no real
    timezone-aware datetime storage: a tz-aware value silently loses its
    tzinfo the moment it round-trips through the DB, while a
    freshly-constructed one keeps it, so mixing the two crashes the first
    time code compares a queried row's timestamp against a fresh one
    (exactly what the policy gate's frequency-cap check does). Every
    timestamp in this system is naive UTC, always — this is the one
    function that should ever call dt.datetime.now()."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_ref: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    email: Mapped[str | None] = mapped_column(String(200))
    dnd: Mapped[bool] = mapped_column(default=False)
    whatsapp_opt_in: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Signal(Base):
    """Raw, immutable event as received from a source adapter — never updated after insert."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), index=True)
    lane: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    exposure_amount: Mapped[int] = mapped_column()  # paise
    raw_payload: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[dt.datetime] = mapped_column(index=True)
    received_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    lane: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(32), default="detected", index=True)
    exposure_amount: Mapped[int] = mapped_column()  # paise
    detected_at: Mapped[dt.datetime] = mapped_column(index=True)
    ladder_rung: Mapped[int] = mapped_column(default=0)
    paused_until: Mapped[dt.datetime | None] = mapped_column()
    # Ground truth for the synthetic simulator only, stamped by detect() from
    # the signal's `_ground_truth_cause` passthrough. /sim's outcome model and
    # /eval's sanity check may read this; diagnose/decide/policy code never
    # may — doing so would make the diagnosis a foregone conclusion instead
    # of something the rules/LLM fallback actually have to get right.
    true_root_cause: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    cause: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(default=1.0)
    evidence: Mapped[dict | None] = mapped_column(JSON)
    method: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class Playbook(Base):
    __tablename__ = "playbooks"

    id: Mapped[int] = mapped_column(primary_key=True)
    lane: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(100))
    version: Mapped[str] = mapped_column(String(32))
    source_path: Mapped[str] = mapped_column(String(255))
    content: Mapped[dict] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(default=True)
    loaded_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    __table_args__ = (UniqueConstraint("lane", "version", name="uq_playbook_lane_version"),)


class Action(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    playbook_id: Mapped[int | None] = mapped_column(ForeignKey("playbooks.id"))
    ladder_rung: Mapped[int] = mapped_column(default=0)
    proposed_action: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str | None] = mapped_column(String(32))
    policy_verdict: Mapped[str] = mapped_column(String(16))
    veto_reason_code: Mapped[str | None] = mapped_column(String(64))
    executed_action: Mapped[str | None] = mapped_column(String(64))
    cost: Mapped[int] = mapped_column(default=0)  # paise
    # Whether the channel actually delivered the message — distinct from
    # policy_verdict: an allowed action can still fail to reach the
    # customer (bounced SMS, no-answer call). None for vetoed actions,
    # since nothing was ever attempted. Read by sim/outcomes.py to decide
    # whether the treatment uplift applies; nothing upstream of it reads
    # this column.
    delivered: Mapped[bool | None] = mapped_column()
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(128))
    # LLM audit trail — populated only when this action's copy/diagnosis involved a model call.
    llm_model: Mapped[str | None] = mapped_column(String(64))
    llm_prompt_template_id: Mapped[str | None] = mapped_column(String(64))
    llm_input_hash: Mapped[str | None] = mapped_column(String(64))
    llm_raw_output: Mapped[str | None] = mapped_column(String)
    llm_latency_ms: Mapped[int | None] = mapped_column()
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)


class Outcome(Base):
    __tablename__ = "outcomes"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    action_id: Mapped[int | None] = mapped_column(ForeignKey("actions.id"))
    outcome_type: Mapped[str] = mapped_column(String(32))
    amount: Mapped[int | None] = mapped_column()  # paise
    promised_date: Mapped[dt.datetime | None] = mapped_column()
    occurred_at: Mapped[dt.datetime] = mapped_column(index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class ExperimentAssignment(Base):
    __tablename__ = "experiment_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), unique=True, index=True)
    arm: Mapped[str] = mapped_column(String(16))
    stratum: Mapped[str] = mapped_column(String(64))
    seed: Mapped[int] = mapped_column()
    assigned_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class CaseEvent(Base):
    """Append-only, hash-chained ledger. Rows are never updated or deleted.

    See db/hashchain.py for how prev_hash/hash are computed — that logic is
    the tamper-evidence, this table is just storage.
    """

    __tablename__ = "case_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow, index=True)
