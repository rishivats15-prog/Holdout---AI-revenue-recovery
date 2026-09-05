"""Rules engine for signal -> root cause diagnosis.

The mapping lives entirely in YAML (one file per lane) so a new lane's
diagnosis table is pure data — adding B2B means writing b2b.yaml here, not
touching this module or any other diagnosis/decide/policy code.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from db.enums import RootCause

DIAGNOSIS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class DiagnosisRule:
    cause: str
    retry_viable: str
    description: str


@dataclass(frozen=True)
class RuleTable:
    lane: str
    safe_fallback_cause: str
    code_to_rule: dict[str, DiagnosisRule]

    @property
    def causes(self) -> set[str]:
        return {rule.cause for rule in self.code_to_rule.values()}


@lru_cache(maxsize=None)
def load_rule_table(lane: str) -> RuleTable:
    path = DIAGNOSIS_DIR / f"{lane}.yaml"
    raw = yaml.safe_load(path.read_text())

    valid_causes = {c.value for c in RootCause}
    code_to_rule: dict[str, DiagnosisRule] = {}
    for entry in raw["rules"]:
        if entry["cause"] not in valid_causes:
            raise ValueError(f"{path}: unknown root cause {entry['cause']!r}")
        rule = DiagnosisRule(
            cause=entry["cause"],
            retry_viable=entry["retry_viable"],
            description=entry["description"].strip(),
        )
        for code in entry["failure_codes"]:
            if code in code_to_rule:
                raise ValueError(f"{path}: failure code {code!r} mapped by more than one rule")
            code_to_rule[code] = rule

    table = RuleTable(
        lane=raw["lane"],
        safe_fallback_cause=raw["safe_fallback_cause"],
        code_to_rule=code_to_rule,
    )
    if table.safe_fallback_cause not in table.causes:
        raise ValueError(f"{path}: safe_fallback_cause must be one of this lane's own causes")
    return table


def diagnose_by_rule(lane: str, failure_code: str | None) -> DiagnosisRule | None:
    """Pure lookup — returns None when there's no clean rule match, which
    is exactly the signal for the caller to fall through to the LLM path."""
    if failure_code is None:
        return None
    return load_rule_table(lane).code_to_rule.get(failure_code)
