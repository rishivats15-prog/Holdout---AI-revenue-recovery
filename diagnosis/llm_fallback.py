"""LLM fallback classifier — one of the only two places in this system a
model is allowed to run (the other is outbound copy generation). Used only
when a signal's failure_code doesn't match any rule: a genuinely ambiguous
free-text decline reason.

This is a Phase 2 stub: the interface is real and already enforces the hard
invariant a live model call would also have to satisfy — the return value
is always one of the causes valid for this lane, never a new label the
model invents, and never a real cause borrowed from another lane's table.
The actual call (_stub_model_call) is hardcoded for now; swapping it for a
live API later changes nothing about that invariant, because the
enforcement lives in coerce_to_enum, not in the model call itself.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass

from db.enums import RootCause
from diagnosis.rules import load_rule_table

PROMPT_TEMPLATE_ID = "diagnosis_fallback_v1"
STUB_MODEL_NAME = "stub-classifier-v0"

ModelCallFn = Callable[[str, str], str]


@dataclass(frozen=True)
class LLMDiagnosisResult:
    cause: str
    confidence: float
    model: str
    prompt_template_id: str
    input_hash: str
    raw_output: str
    latency_ms: int


def classify_ambiguous_signal(
    free_text: str, lane: str, model_call: ModelCallFn | None = None
) -> LLMDiagnosisResult:
    model_call = model_call or _stub_model_call
    started = time.monotonic()
    raw_output = model_call(free_text, lane)
    latency_ms = int((time.monotonic() - started) * 1000)

    cause = coerce_to_enum(raw_output, lane)

    return LLMDiagnosisResult(
        cause=cause.value,
        confidence=0.55,
        model=STUB_MODEL_NAME,
        prompt_template_id=PROMPT_TEMPLATE_ID,
        input_hash=hashlib.sha256(free_text.encode("utf-8")).hexdigest(),
        raw_output=str(raw_output),
        latency_ms=latency_ms,
    )


def coerce_to_enum(raw_output: str | None, lane: str) -> RootCause:
    """The actual guardrail. Whatever a model (real or stubbed) returns,
    this always returns a RootCause valid for the given lane — falling
    back to that lane's declared safe_fallback_cause for anything else:
    garbage, an empty string, or a real cause that just belongs to a
    different lane's table.
    """
    valid_for_lane = load_rule_table(lane).causes

    if raw_output:
        try:
            candidate = RootCause(raw_output.strip().lower())
        except ValueError:
            candidate = None
        if candidate is not None and candidate.value in valid_for_lane:
            return candidate

    return RootCause(load_rule_table(lane).safe_fallback_cause)


def _stub_model_call(free_text: str, lane: str) -> str:
    """Placeholder for a real model call. A deliberately simple keyword
    heuristic — Phase 2 only needs the interface and the enforcement
    around it, not a good classifier.
    """
    text = free_text.lower()
    if "bank" in text or "card" in text:
        return RootCause.STALE_CREDENTIAL.value
    if "fraud" in text or "risk" in text:
        return RootCause.RISK_FRAUD.value
    return RootCause.ISSUER_SOFT_DECLINE.value
