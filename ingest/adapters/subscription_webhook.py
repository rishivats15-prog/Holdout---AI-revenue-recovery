"""Normalizes a subscription-lane payment-failure webhook into a Signal row.

This is the one adapter for the 'payment_webhook' source, subscription lane.
A new source or lane gets its own adapter module here — nothing downstream
(detect, diagnose, policy) needs to change to support it.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from db.enums import Lane, SignalSource
from db.models import Customer, Signal


def ingest_subscription_webhook(db: Session, raw_event: dict) -> Signal:
    payment = raw_event["payload"]["payment"]
    customer = _resolve_customer(db, payment["customer_id"])

    signal = Signal(
        customer_id=customer.id,
        lane=Lane.SUBSCRIPTION.value,
        source=SignalSource.PAYMENT_WEBHOOK.value,
        failure_code=payment.get("error_reason"),
        exposure_amount=payment["amount"],
        raw_payload=raw_event,
        # naive UTC, matching db.models.utcnow — see its docstring for why
        occurred_at=dt.datetime.fromtimestamp(payment["created_at"], tz=dt.timezone.utc).replace(tzinfo=None),
    )
    db.add(signal)
    db.flush()
    return signal


def _resolve_customer(db: Session, external_ref: str) -> Customer:
    customer = db.query(Customer).filter_by(external_ref=external_ref).one_or_none()
    if customer is None:
        raise ValueError(
            f"unknown customer external_ref={external_ref!r}; "
            "customers must be provisioned before their signals arrive"
        )
    return customer
