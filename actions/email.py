"""Fake email adapter. Not used by the current subscription playbook, but
built per the repo structure's channel list — a B2B lane (invoice
reminders) is the likely first real user. Logs a payload shaped like a
real transactional-email API call; nothing leaves the process.
"""

from __future__ import annotations

import random

from actions.base import ExecutionResult, fake_external_id
from db.models import Case, Customer
from decide.decide import ProposedAction

FAILURE_RATE = 0.05


def send(case: Case, customer: Customer, proposal: ProposedAction, rng: random.Random) -> ExecutionResult:
    request_payload = {
        "api": "email.send",
        "to": customer.email,
        "template_id": proposal.copy_template,
        "case_id": case.id,
    }
    success = rng.random() >= FAILURE_RATE
    external_id = fake_external_id("email", rng)
    response_payload = {"id": external_id, "status": "delivered" if success else "bounced"}
    return ExecutionResult(
        external_id=external_id, success=success, request_payload=request_payload, response_payload=response_payload
    )
