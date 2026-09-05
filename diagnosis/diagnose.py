"""Diagnose step — maps each case's most recent signal to a root cause.

Rules first: a clean failure_code always resolves via the lane's YAML
rules table. Only a genuinely ambiguous signal (no clean failure_code)
falls through to the LLM classifier, which is hard-constrained to answer
with one of this lane's existing causes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from db.enums import CaseState, DiagnosisMethod
from db.hashchain import append_case_event
from db.models import Case, Diagnosis, Signal
from diagnosis.llm_fallback import classify_ambiguous_signal
from diagnosis.rules import diagnose_by_rule


def diagnose_case(db: Session, case: Case) -> Diagnosis:
    signal = _latest_signal(db, case)
    rule = diagnose_by_rule(case.lane, signal.failure_code)

    if rule is not None:
        diagnosis = Diagnosis(
            case_id=case.id,
            cause=rule.cause,
            confidence=1.0,
            method=DiagnosisMethod.RULE.value,
            evidence={
                "signal_id": signal.id,
                "failure_code": signal.failure_code,
                "retry_viable": rule.retry_viable,
                "rule_description": rule.description,
            },
        )
    else:
        free_text = signal.raw_payload["payload"]["payment"].get("error_description", "")
        result = classify_ambiguous_signal(free_text, case.lane)
        diagnosis = Diagnosis(
            case_id=case.id,
            cause=result.cause,
            confidence=result.confidence,
            method=DiagnosisMethod.LLM_FALLBACK.value,
            evidence={
                "signal_id": signal.id,
                "free_text_input": free_text,
                "model": result.model,
                "prompt_template_id": result.prompt_template_id,
                "input_hash": result.input_hash,
                "raw_output": result.raw_output,
                "latency_ms": result.latency_ms,
            },
        )

    db.add(diagnosis)
    case.state = CaseState.DIAGNOSED.value
    db.flush()
    append_case_event(
        db,
        case.id,
        "case_diagnosed",
        {"cause": diagnosis.cause, "method": diagnosis.method, "confidence": diagnosis.confidence},
    )
    return diagnosis


def diagnose_all_detected(db: Session) -> int:
    """Diagnoses every case currently sitting in 'detected'. Returns how
    many were diagnosed."""
    cases = db.query(Case).filter(Case.state == CaseState.DETECTED.value).all()
    for case in cases:
        diagnose_case(db, case)
    db.commit()
    return len(cases)


def _latest_signal(db: Session, case: Case) -> Signal:
    return (
        db.query(Signal)
        .filter(Signal.case_id == case.id)
        .order_by(Signal.occurred_at.desc())
        .first()
    )
