"""Runs the batch report against the seeded database and prints it.

    python -m scripts.report            # human-readable report
    python -m scripts.report --json     # the same figures as JSON

Read-only: it opens the database, computes, prints, and writes nothing.
Exits non-zero if the ground-truth check fails — so `make report` doubles
as the honesty test for the measurement layer, not just a display.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from db.session import DB_PATH, SessionLocal
from eval.render import render_report
from eval.report import generate_batch_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch report for the current database.")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON instead of text")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"No database at {DB_PATH}. Run `make seed` first.", file=sys.stderr)
        return 2

    db = SessionLocal()
    try:
        report = generate_batch_report(db)
    finally:
        db.close()

    if args.json:
        print(json.dumps(dataclasses.asdict(report), indent=2, default=str))
    else:
        print(render_report(report))

    ground_truth = report.ground_truth
    if ground_truth is not None and ground_truth.expected_within_ci is False:
        print(
            "\nGROUND-TRUTH CHECK FAILED: the true simulated effect falls outside the measured "
            "confidence interval. The measurement layer, the outcome model, or both are wrong.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
