"""Fake retry adapter — simulates re-attempting the charge through the
payment gateway. Logs a payload shaped like a real gateway retry call;
nothing leaves the process.
"""

from __future__ import annotations

import random

from actions.base import ExecutionResult, fake_external_id
from db.models import Case, Customer
from decide.decide import ProposedAction

FAILURE_RATE = 0.10


def send(case: Case, customer: Customer, proposal: ProposedAction, rng: random.Random) -> ExecutionResult:
    request_payload = {
        "api": "payments.retry",
        "case_id": case.id,
        "customer_ref": customer.external_ref,
        "amount": case.exposure_amount,
        "currency": "INR",
        "reason": proposal.action_type,
    }
    success = rng.random() >= FAILURE_RATE
    external_id = fake_external_id("retry", rng)
    response_payload = {"id": external_id, "status": "processing" if success else "gateway_unavailable"}
    return ExecutionResult(
        external_id=external_id, success=success, request_payload=request_payload, response_payload=response_payload
    )
