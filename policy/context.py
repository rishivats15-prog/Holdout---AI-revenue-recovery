"""Shared shapes for policy evaluation: the input every check reads
(PolicyContext), the output the gate returns (PolicyVerdict), and the
batch-wide CircuitBreaker state that persists across an entire run.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from db.models import Action, Case, Customer
from decide.decide import ProposedAction


@dataclass
class CircuitBreaker:
    """Trips once a batch's cumulative channel-delivery failure rate
    crosses failure_threshold, after at least min_sample attempts have
    been made. Once tripped it stays tripped for the rest of the run —
    Phase 5's orchestrator owns any reset-between-runs behaviour.
    """

    failure_threshold: float = 0.30
    min_sample: int = 20
    attempts: int = 0
    failures: int = 0
    tripped: bool = False

    def record_outcome(self, success: bool) -> None:
        self.attempts += 1
        if not success:
            self.failures += 1
        if self.attempts >= self.min_sample and (self.failures / self.attempts) > self.failure_threshold:
            self.tripped = True

    def is_open(self) -> bool:
        return self.tripped


@dataclass(frozen=True)
class PolicyContext:
    case: Case
    customer: Customer
    proposal: ProposedAction
    prior_actions: list[Action]
    now: dt.datetime
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)


@dataclass(frozen=True)
class PolicyVerdict:
    allowed: bool
    reason_code: str | None
    checks_evaluated: tuple[str, ...]
