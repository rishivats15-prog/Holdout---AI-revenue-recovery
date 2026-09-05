"""Fake SMS adapter. Logs a payload shaped like a real SMS-gateway send
call; nothing leaves the process.
"""

from __future__ import annotations

import random

from actions.base import ExecutionResult, fake_external_id
from db.models import Case, Customer
from decide.decide import ProposedAction

FAILURE_RATE = 0.02


def send(case: Case, customer: Customer, proposal: ProposedAction, rng: random.Random) -> ExecutionResult:
    request_payload = {
        "api": "sms.send",
        "to": customer.phone,
        "template_id": proposal.copy_template,
        "case_id": case.id,
    }
    success = rng.random() >= FAILURE_RATE
    external_id = fake_external_id("sms", rng)
    response_payload = {"id": external_id, "status": "delivered" if success else "undelivered"}
    return ExecutionResult(
        external_id=external_id, success=success, request_payload=request_payload, response_payload=response_payload
    )
