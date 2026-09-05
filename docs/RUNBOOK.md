# Runbook

## Kill switch — read this first

Every outbound action (SMS, WhatsApp, voice, retry, email) passes through the
policy gate before execution, and the gate's **first** check is the kill switch.

**To halt all outbound action immediately, run from the repo root:**

```
make halt
```

**To resume:**

```
make resume
```

**To check without changing anything:**

```
make status
```

`make halt` is a wrapper around `python -m scripts.halt`. If neither the venv nor
Make is available, this does the same thing:

```
touch KILL_SWITCH        # halt
rm KILL_SWITCH           # resume
```

The flag *is* the file's existence, not its contents — a bare `touch` is a fully
valid engaged switch. `make halt` additionally records who engaged it and when,
which is what the dashboard's halt band displays.

**Who can run it:** anyone with shell access to the deployment. This is a
synthetic-data screening submission, not a production system; a real deployment
would put this behind an on-call/ops control rather than a bare file check.

**What happens while it's engaged:** every proposed action is vetoed with reason
code `KILL_SWITCH_ENGAGED`, in every lane, regardless of playbook or case state.
Nothing is deleted or rolled back — cases stay where they are and pick back up on
the next tick once the switch is lifted.

**One flag, three readers.** The CLI, the dashboard's halt button, and the policy
gate all resolve the same path through the same predicate in
`policy/killswitch.py`. The rail indicator and the gate cannot disagree; there is
a test that fails if they ever do
(`tests/test_killswitch.py::test_the_policy_gate_reads_the_same_flag_the_cli_sets`).

**From the dashboard:** the kill switch sits at the base of the left rail on
every page. It requires typing `HALT` to confirm. The typed confirmation is
validated on the server, so the control cannot be bypassed by disabling
JavaScript. Once engaged, a band sits across the top of every page and the rail
indicator turns to `HALTED`.

## Circuit breaker

Automatic rather than operator-triggered. If cumulative channel-delivery failures
in a run exceed **30% after at least 20 attempts**, the breaker trips and every
subsequent action is vetoed with `CIRCUIT_BREAKER_OPEN` for the rest of that run.
It does not reset mid-run; a fresh `make seed` starts with a closed breaker.
Thresholds live in `policy/context.py:CircuitBreaker`.

The dashboard rail shows the breaker's state for the batch in the database: the
observed delivery-failure rate, and whether any action was actually vetoed with
`CIRCUIT_BREAKER_OPEN`.

## Running the project

```
make install    # first time only — creates .venv and installs dependencies
make demo       # fresh batch + batch report + dashboard on http://127.0.0.1:2000
```

The dashboard serves on **port 2000** by default. If that port is taken:

```
make demo PORT=8000
```

`make demo` is the one command for a cold start. The individual steps:

```
make seed       # generate a batch and run it forward 21 simulated days
make report     # print the batch report; exits non-zero if the
                #   ground-truth check fails
make report-json
make run        # dashboard only, with autoreload (assumes a batch exists)
make test       # the full test suite
```

## The live run page

`/run` drives the pipeline **one simulated day per click** — the clearest way to
show the system working rather than showing its aftermath.

- **Generate a batch** creates 900 customers and their failed payments, folds
  retries into deduplicated cases, diagnoses each, and randomizes the arms —
  but runs no treatment. The run sits at day zero.
- **Advance one day / Advance five / Run to the end** step the orchestrator.
  Case-state counters, running totals, the two activity charts, and the per-day
  table all update on each click.

It is not a demo mode. The page calls the same `RunSession.advance` that
`make seed` calls in a loop, with the same seeds, against the same database —
stepping all 21 days by hand lands on a database identical to the one `make seed`
produces in one pass. `tests/test_live_run.py::test_stepping_day_by_day_lands_where_the_batch_runner_lands`
fails if that ever stops being true.

**Two limits, stated rather than hidden:**

- The run's RNG and circuit breaker are held in memory by the server process.
  Restarting the server abandons a run in progress.
- Opening `/run` after `make seed` shows the finished batch with no steps to
  take, because that run happened in a different process. Generate a new batch
  to step one.

**Best demo move:** engage the kill switch mid-run, advance a day, and watch it
produce nothing but vetoes. Then resume and continue.

## Recording the submission video

`docs/DEMO_SCRIPT.md` is the beat sheet: pre-flight checklist, timed beats with
the exact clicks and narration, the numbers you should see on screen, the three
cases worth featuring, and what to do if something goes wrong mid-take.

## Walking a judge through the dashboard

1. **`/` — Scoreboard.** The hero is the flow of money: exposure at risk splits
   into treated and holdout, each splits into recovered and not. Band thickness
   is rupees throughout. The bracketed terracotta strip is the incremental
   recovery — the only part of the gross figure the experiment attributes to
   treatment. Below it, the figure row leads with that incremental number; gross
   recovery is deliberately set at lower weight. Below that, the measured
   confidence interval with the *known* true effect marked on it.
2. **`/vetoes` — Veto log.** Start with "Every bound in the gate": all ten
   bounds, what each enforces, and how many times it fired in this batch,
   including the zeros. Then the log itself, filterable by reason code.
3. **`/run` — Live run.** Generate a batch and step it. This is the beat that
   makes everything else legible.
4. **`/cases/4` — Case replay** (any case with a veto works). Read it top to
   bottom. Where the gate stopped an action, the spine breaks and a hatched
   block sits across it with the reason code. "Rules evaluated" expands to show
   every bound checked before the one that fired. The right column carries the
   diagnosis with its provenance and the hash-chained ledger with a
   verified/broken indicator.

## Reproducibility

Every run is seeded. Same `sim/ground_truth.yaml` in, same customers, signals,
cases, and arm assignments out. `make seed` always starts from a clean database
file so a batch can never be half-regenerated.

The batch number in the status bar (`N° BATCH / 0820`) is the last four digits of
that seed.

## Database

SQLite, file-based, at `revenue_recovery.db` in the repo root. No migration tool
— the schema is created by `Base.metadata.create_all()` on startup
(`db/session.py:init_db`). To reset from scratch:

```
rm revenue_recovery.db && make seed
```

## When something looks wrong

| Symptom | Where to look |
|---|---|
| `make report` exits non-zero | The ground-truth check failed: the true effect fell outside the measured CI. Either the outcome model or the measurement is wrong — start at `eval/groundtruth.py`. |
| Dashboard shows no cases | The database is empty or missing. Run `make seed`. |
| Every action is vetoed | Check `make status`. The kill switch may be engaged. |
| A case's hash chain reads BROKEN | Someone edited or deleted a `case_events` row. The replay page names the first bad event id. |
| Numbers differ between CLI and dashboard | They shouldn't — both render the same `BatchReport` object through the same money formatter. File it as a bug. |
