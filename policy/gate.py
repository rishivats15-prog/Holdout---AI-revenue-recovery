"""The policy gate — runs every check in order, stops at the first veto.

Order matters only for which single reason code gets logged when more than
one check would fire; global halts (kill switch, circuit breaker) go
first, then structural validity, then the case-specific bounds roughly in
the order CLAUDE.md lists them.
"""

from __future__ import annotations

from collections.abc import Callable

from policy import checks
from policy.context import PolicyContext, PolicyVerdict

# Bump when a check is added, removed, or its threshold changes. The
# dashboard stamps this on every page and every replayed action, so a
# vetoed action can always be traced to the ruleset that vetoed it.
POLICY_VERSION = "1.0"

CheckFn = Callable[[PolicyContext], "str | None"]

CHECKS: tuple[tuple[str, CheckFn], ...] = (
    ("kill_switch", checks.check_kill_switch),
    ("circuit_breaker", checks.check_circuit_breaker),
    ("action_allowlist", checks.check_action_allowlist),
    ("quiet_hours", checks.check_quiet_hours),
    ("frequency_caps", checks.check_frequency_caps),
    ("consent_and_eligibility", checks.check_consent_and_eligibility),
    ("financial_authority", checks.check_financial_authority),
    ("cost_ceiling", checks.check_cost_ceiling),
    ("expected_value_floor", checks.check_expected_value_floor),
    ("content_rules", checks.check_content_rules),
)


def evaluate_policy(ctx: PolicyContext) -> PolicyVerdict:
    evaluated: list[str] = []
    for name, check_fn in CHECKS:
        evaluated.append(name)
        reason = check_fn(ctx)
        if reason is not None:
            return PolicyVerdict(allowed=False, reason_code=reason, checks_evaluated=tuple(evaluated))
    return PolicyVerdict(allowed=True, reason_code=None, checks_evaluated=tuple(evaluated))
