"""Shared shapes for channel adapters. Every adapter returns the same
ExecutionResult, so Execute doesn't care which channel it called — and
every adapter is fake: it builds a payload that looks like a real API
call, "sends" it by simulating a probabilistic delivery outcome, and
touches nothing external.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionResult:
    external_id: str
    success: bool
    request_payload: dict
    response_payload: dict


def fake_external_id(prefix: str, rng: random.Random) -> str:
    return f"{prefix}_{rng.getrandbits(64):016x}"
