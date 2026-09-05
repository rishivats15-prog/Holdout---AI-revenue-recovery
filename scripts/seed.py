"""Wipes and recreates the database, then generates one fresh synthetic
batch for the subscription lane (Phase 1 scope — B2B lane comes later as a
second playbook + adapter, per the handover test in CLAUDE.md) and runs it
forward through the full case lifecycle: detect -> diagnose -> stratified
holdout assignment -> the orchestrator's ticked decide/execute/observe
loop, including promise-to-pay pause/break handling.

Experiment assignment happens right after diagnosis and before any
treatment, so which arm a case lands in can never be influenced by
anything that happens to it afterwards — see eval/assignment.py.

Always starts from a clean file so a batch is reproducible: same seed
(sim/ground_truth.yaml) in, same customers/signals/cases out.
"""

from db.session import DB_PATH, SessionLocal, init_db
from diagnosis.diagnose import diagnose_all_detected
from eval.assignment import assign_experiment_arms
from orchestrator.runner import run_orchestrator
from sim.generator import generate_batch


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()

    db = SessionLocal()
    try:
        stats = generate_batch(db)
        diagnosed = diagnose_all_detected(db)
        assignment_stats = assign_experiment_arms(db)
        run_stats = run_orchestrator(db)
    finally:
        db.close()

    print(
        f"Seeded {stats['customers']} customers, {stats['signals']} signals, {diagnosed} diagnoses.\n"
        f"Assigned {assignment_stats['cases_assigned']} cases across {assignment_stats['strata']} strata: "
        f"{assignment_stats['treatment']} treatment, {assignment_stats['holdout']} holdout.\n"
        f"Orchestrator ran {run_stats['ticks']} ticks: "
        f"{run_stats['executed']} executed, {run_stats['vetoed']} vetoed, "
        f"{run_stats['routed']} routed to human queue, {run_stats['skipped_rungs']} rungs skipped, "
        f"{run_stats['recovered']} recovered ({run_stats['recovered_while_paused']} of those while paused), "
        f"{run_stats['paused']} paused, {run_stats['disputed']} disputed, "
        f"{run_stats['written_off']} written off, {run_stats['suppressed']} suppressed, "
        f"{run_stats['promises_broken']} promises broken "
        f"(circuit breaker tripped={run_stats['circuit_breaker_tripped']})."
    )


if __name__ == "__main__":
    main()
