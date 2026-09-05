"""Fake voice/IVR adapter. Logs a payload shaped like a real outbound-call
API request (Hinglish IVR script); nothing leaves the process. Highest
failure rate of the channels — a no-answer is the common case for voice.
"""

from __future__ import annotations

import random

from actions.base import ExecutionResult, fake_external_id
from db.models import Case, Customer
from decide.decide import ProposedAction

FAILURE_RATE = 0.15


def send(case: Case, customer: Customer, proposal: ProposedAction, rng: random.Random) -> ExecutionResult:
    request_payload = {
        "api": "voice.ivr.call",
        "to": customer.phone,
        "script_id": proposal.copy_template,
        "language": "hi-en",
        "case_id": case.id,
    }
    success = rng.random() >= FAILURE_RATE
    external_id = fake_external_id("call", rng)
    response_payload = {"id": external_id, "status": "completed" if success else "no_answer"}
    return ExecutionResult(
        external_id=external_id, success=success, request_payload=request_payload, response_payload=response_payload
    )
