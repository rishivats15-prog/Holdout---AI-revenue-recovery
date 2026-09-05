"""Synthetic subscription-lane batch generator.

Creates a batch of customers and their payment-failure episodes with the
root-cause distribution declared in sim/ground_truth.yaml, emits one
webhook-shaped event per retry attempt, and runs each through the
subscription webhook adapter and the detect step — the same path a real
gateway's webhooks would take.

The true root cause for each episode is stamped into the raw payload as
`_ground_truth_cause`, a field no real gateway would ever send. detect()
copies it onto the case so /sim's later outcome model and /eval's sanity
check can compare the system's diagnosis against reality; no decision code
(diagnose/decide/policy) may read it.
"""

from __future__ import annotations

import datetime as dt
import math
import random

from sqlalchemy.orm import Session

from db.models import Customer, utcnow
from ingest.adapters.subscription_webhook import ingest_subscription_webhook
from ingest.detect import detect_from_signal
from sim.config import GroundTruth, load_ground_truth
from sim.failure_codes import AMBIGUOUS_MESSAGES, FAILURE_CODES

FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditi", "Diya", "Rohan", "Isha", "Kabir", "Meera",
    "Arjun", "Ananya", "Sai", "Riya", "Vihaan", "Sanya", "Karan", "Priya",
]
LAST_NAMES = [
    "Sharma", "Verma", "Iyer", "Nair", "Gupta", "Reddy", "Khan", "Joshi",
    "Mehta", "Rao", "Kapoor", "Chatterjee", "Bose", "Menon", "Pillai",
]


def generate_batch(db: Session, n_customers: int = 900, now: dt.datetime | None = None) -> dict:
    gt = load_ground_truth()
    rng = random.Random(gt.seed)
    now = now or utcnow()

    causes = list(gt.root_cause_distribution.keys())
    weights = list(gt.root_cause_distribution.values())

    signal_count = 0
    for i in range(n_customers):
        customer = _make_customer(db, rng, i)
        cause = rng.choices(causes, weights=weights, k=1)[0]
        ambiguous = rng.random() < gt.ambiguous_signal_rate
        exposure_paise = _sample_exposure_paise(rng, gt)
        n_retries = rng.randint(gt.retries_min, gt.retries_max)
        # Backdated by a few days at most, not weeks — detected_at anchors
        # the 14-day attribution window (CLAUDE.md), and the orchestrator
        # only ticks forward from "today." A wide backdating spread would
        # let a case's window already be half-spent (or fully elapsed)
        # before treatment ever got a chance to run against it.
        episode_start = now - dt.timedelta(days=rng.uniform(0, 3))

        for attempt in range(1, n_retries + 1):
            occurred_at = episode_start + dt.timedelta(hours=(attempt - 1) * rng.uniform(6, 30))
            raw_event = _build_webhook(customer, attempt, exposure_paise, cause, ambiguous, occurred_at, rng)
            signal = ingest_subscription_webhook(db, raw_event)
            detect_from_signal(db, signal)
            signal_count += 1

    db.commit()
    return {"customers": n_customers, "signals": signal_count}


def _make_customer(db: Session, rng: random.Random, index: int) -> Customer:
    first = rng.choice(FIRST_NAMES)
    last = rng.choice(LAST_NAMES)
    customer = Customer(
        external_ref=f"cust_{index:06d}",
        name=f"{first} {last}",
        phone=f"9{rng.randint(100000000, 999999999)}",
        email=f"{first.lower()}.{last.lower()}{index}@example.com",
        dnd=rng.random() < 0.08,
        whatsapp_opt_in=rng.random() < 0.62,
    )
    db.add(customer)
    db.flush()
    return customer


def _sample_exposure_paise(rng: random.Random, gt: GroundTruth) -> int:
    mu = math.log(gt.exposure_median_paise)
    value = math.exp(rng.gauss(mu, gt.exposure_sigma))
    return int(min(max(value, gt.exposure_min_paise), gt.exposure_max_paise))


def _build_webhook(
    customer: Customer,
    attempt: int,
    exposure_paise: int,
    cause: str,
    ambiguous: bool,
    occurred_at: dt.datetime,
    rng: random.Random,
) -> dict:
    if ambiguous:
        error_reason = None
        error_description = rng.choice(AMBIGUOUS_MESSAGES).format(ticket=rng.randint(10000, 99999))
    else:
        error_reason = rng.choice(FAILURE_CODES[cause])
        error_description = error_reason.replace("_", " ").title()

    return {
        "event": "payment.failed",
        "payload": {
            "payment": {
                "id": f"pay_{customer.external_ref}_{attempt}",
                "subscription_id": f"sub_{customer.external_ref}",
                "customer_id": customer.external_ref,
                "amount": exposure_paise,
                "currency": "INR",
                "method": rng.choice(["card", "upi_autopay", "card", "card"]),
                "error_reason": error_reason,
                "error_description": error_description,
                "attempt_number": attempt,
                "created_at": int(occurred_at.timestamp()),
            }
        },
        "_ground_truth_cause": cause,
    }
