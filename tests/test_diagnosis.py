"""Diagnosis engine tests.

The one invariant Phase 2 is explicitly graded on: no matter what a model
(real or stubbed) returns, the LLM fallback path can never emit a value
outside the existing root-cause enum for that lane.
"""

from __future__ import annotations

import pytest

from db.enums import RootCause
from diagnosis.llm_fallback import classify_ambiguous_signal, coerce_to_enum
from diagnosis.rules import diagnose_by_rule, load_rule_table
from sim.failure_codes import FAILURE_CODES

GARBAGE_MODEL_OUTPUTS = [
    "definitely_not_a_real_cause",
    "insufficient_funds_probably",
    "",
    "   ",
    None,
    "PO_GRN_MISMATCH",  # a real enum value, but from the b2b lane, not subscription
    "risk_fraud; drop table diagnoses;",
]


@pytest.mark.parametrize("garbage", GARBAGE_MODEL_OUTPUTS)
def test_coerce_to_enum_never_returns_out_of_enum_value(garbage):
    result = coerce_to_enum(garbage, lane="subscription")
    assert isinstance(result, RootCause)
    assert result.value in load_rule_table("subscription").causes


@pytest.mark.parametrize("garbage", GARBAGE_MODEL_OUTPUTS)
def test_classify_ambiguous_signal_never_emits_out_of_enum_value(garbage):
    result = classify_ambiguous_signal(
        "some ambiguous decline message",
        lane="subscription",
        model_call=lambda text, lane: garbage,
    )
    assert result.cause in {c.value for c in RootCause}
    assert result.cause in load_rule_table("subscription").causes


def test_valid_causes_pass_through_coercion_unchanged():
    for cause in load_rule_table("subscription").causes:
        assert coerce_to_enum(cause, lane="subscription").value == cause


def test_safe_fallback_cause_is_itself_valid_for_its_lane():
    table = load_rule_table("subscription")
    assert table.safe_fallback_cause in table.causes


def test_diagnose_by_rule_returns_none_for_unmapped_or_missing_code():
    assert diagnose_by_rule("subscription", None) is None
    assert diagnose_by_rule("subscription", "TOTALLY_UNKNOWN_CODE") is None


def test_rules_table_matches_the_generators_failure_code_taxonomy():
    """Ties Phase 1's synthetic failure codes to Phase 2's diagnosis rules:
    every code the generator can emit for a cause must map back to that
    same cause, or the two phases have silently drifted apart."""
    for cause, codes in FAILURE_CODES.items():
        for code in codes:
            rule = diagnose_by_rule("subscription", code)
            assert rule is not None, f"no rule matches generator code {code!r}"
            assert rule.cause == cause


def test_no_failure_code_is_claimed_by_more_than_one_cause():
    table = load_rule_table("subscription")
    codes = list(table.code_to_rule.keys())
    assert len(codes) == len(set(codes))
