# Holdout

**AI revenue recovery for failed subscription payments — measured against a randomised control group.**

When a recurring payment fails, most systems retry it and send reminders. Some
money comes back, but nobody can say how much of it would have come back anyway.
So recovery tools report *gross* recovery, which is largely a measure of how many
customers were going to pay regardless.

Holdout reports **incremental** recovery: treated minus a randomised holdout,
with a confidence interval and a fixed attribution window. Every outbound action
passes a deterministic policy gate that can veto it, and every veto is logged
with a reason code against the case.

The design rule the whole codebase is built around:

> The LLM proposes, a deterministic policy engine disposes, and a holdout group
> decides whether any of it worked. Nothing that touches money, compliance, or
> the scoreboard runs through a model.

---

## Run it

Python 3.11+. No Node, no npm, no build step, no external services.

```bash
make install     # creates .venv, installs 9 dependencies
make demo        # seeds a batch, prints the report, serves the dashboard
```

Then open **http://127.0.0.1:2000**.

`make demo` runs three things in sequence — you can also run them separately:

| Command | What it does |
|---|---|
| `make seed` | Wipes the DB, generates 900 synthetic cases, runs them forward 21 simulated days |
| `make report` | Prints the batch report. **Exits non-zero if the ground-truth check fails** |
| `make run` | Serves the dashboard (`make run PORT=8000` to change port) |
| `make test` | 264 tests |
| `make halt` / `make resume` | The kill switch, from the CLI |

Every `make` target is a one-line shortcut — the raw command is in the
[Makefile](Makefile) if you'd rather run it directly.

### What to look at first

| Route | Why |
|---|---|
| `/run` | Advance the pipeline **one simulated day per click** and watch cases move through the state machine |
| `/` | The scoreboard — incremental recovery, and the measurement checked against known truth |
| `/cases/320` | One case end to end, including three actions the policy gate refused |
| `/vetoes` | All ten policy bounds and how often each fired |

---

## Why the data is synthetic

There is no payment-gateway sandbox behind this, and that is a deliberate design
choice rather than a limitation.

Against a live gateway there is no true treatment effect to check a measurement
against — a confidence interval is something you either trust or you don't. Here
the true effect is written down in [`sim/ground_truth.yaml`](sim/ground_truth.yaml)
*before* anything is measured, so the report can show the measured lift landing
on it. On the canonical batch:

```
Measured incremental rate   9.00%   95% CI [1.39%, 16.61%]
True simulated effect       11.88%
Verdict                     PASS — true effect lies inside the measured CI
```

Two corrections separate the raw YAML uplift from what any report could measure,
and both are arithmetic rather than judgement:

1. **Attribution window.** The simulator schedules a recovering case's payment
   uniformly across days 1–20; the report counts only 14. So exactly 70% of
   recoveries are ever countable — in *both* arms — which scales the observable
   lift down by the same factor.
2. **Intent-to-treat.** The uplift applies by *arm*, not by whether a message was
   delivered. A treated case that the policy gate suppressed at rung 0 stays in
   the denominator, because a randomised holdout can only support an ITT
   estimate. Excluding it would be marking our own homework against a population
   the experiment never isolated.

`tests/test_groundtruth.py` verifies the estimator is unbiased independently, on
a 20,000-case population — so a single batch landing inside its interval isn't
just luck.

---

## Architecture

Six layers. Each is a directory; each does one thing.

```
signal → detect → diagnose → decide → execute → measure
```

**1. Signals in** (`ingest/`) — one adapter per source normalises every failure
into a single immutable `signals` row before anything else touches it.

**2. Detect** (`ingest/detect.py`) — pure rules. Folds signals into cases,
deduplicating hard: a customer's three failed retries within one open episode
become **one** case, not three.

**3. Diagnose** (`diagnosis/`) — maps the failure to a root cause. Clean failure
codes resolve through a YAML table ([`diagnosis/subscription.yaml`](diagnosis/subscription.yaml));
only genuinely ambiguous free text falls through to an LLM classifier, which is
hard-constrained to the existing enum. The coercion happens in `coerce_to_enum`,
not in the model call, so a model that invents a label cannot produce one.

**4. Decide** (`decide/`) — looks up the ordered treatment ladder for this cause
and rung from the lane's playbook. `decide.py` never branches on a cause by name;
it only ever asks "what is rung N for this cause?"

**5. Execute** (`actions/`, `policy/`) — the policy gate evaluates ten bounds,
then a channel adapter either sends (fake — logs a realistic payload, hits
nothing) or the action is written as vetoed with a reason code. Idempotency keys
on every attempt.

**6. Measure** (`eval/`) — stratified holdout assignment and the batch report.

### Case state machine

```
detected → diagnosed → in_treatment → recovered | written_off | human_queue | suppressed
                            ↕
                         paused          (promise-to-pay)
```

`paused` is non-terminal. A promise to pay freezes the case until the promised
date plus a grace window; breaking it re-enters treatment **one rung higher**
rather than starting the ladder over — a consequence of the state machine, not a
special case bolted onto it.

---

## The policy gate

Ten bounds, evaluated in order by [`policy/gate.py`](policy/gate.py). The first to
object stops the send; its reason code and the full list of checks evaluated up to
that point are written to the action row.

| # | Check | Bound |
|---|---|---|
| 1 | `kill_switch` | One command halts every outbound action |
| 2 | `circuit_breaker` | >30% delivery failure (min 20 attempts) halts outbound automatically |
| 3 | `action_allowlist` | Actions come from the playbook; the agent cannot invent one |
| 4 | `quiet_hours` | No contact outside 8am–7pm |
| 5 | `frequency_caps` | One touch per channel per day, plus per-rung cooldowns |
| 6 | `consent_and_eligibility` | DND, WhatsApp opt-in, DLT template registration |
| 7 | `financial_authority` | Waivers above ₹500 need human sign-off |
| 8 | `cost_ceiling` | Cumulative spend capped at 8% of exposure |
| 9 | `expected_value_floor` | Stop when p(recover) × exposure < the next touch's cost |
| 10 | `content_rules` | No legal threats, no third-party disclosure |

Each is a plain function — `PolicyContext` in, a reason code or `None` out — with
its own passing and failing tests. `/vetoes` lists **all ten with their firing
counts, including the zeros**, because a log showing only the codes that fired
invites the question of whether the rest are wired at all.

The kill switch is one flag with three readers — the CLI, the dashboard button,
and the gate's first check all resolve the same path through
[`policy/killswitch.py`](policy/killswitch.py). A test fails if they ever disagree.

---

## Measurement

- **Stratified assignment.** 15% holdout, stratified by *diagnosed* cause × exposure
  band, assigned once immediately after diagnosis and before any treatment — so an
  arm can never be influenced by what happens to the case afterwards. Stratifying
  on the diagnosed cause rather than the true one is deliberate: a real experiment
  can only stratify on what it can observe.
- **Fixed 14-day attribution window** from detection. A payment outside it is never
  counted — not as a late recovery, not at a discount — even where the case row
  says `recovered`.
- **Reported:** incremental recovery rate and rupees, two-proportion confidence
  interval, cost per ₹100 (against incremental *and* gross), net of channel cost,
  median days to cash, touches per recovery, per-cause and per-channel breakdowns,
  and harm metrics (opt-out, complaint, veto rate) treated vs holdout.

`eval/report.py` is split so that `load_case_records` is the only part touching the
database and `build_report` is pure arithmetic over a list of dataclasses — every
figure is reproducible on paper against hand-built records.

Two things the report says out loud rather than hiding:

- The holdout's zero complaint and veto rates are **structural, not measured** —
  nothing was ever sent to it. That asymmetry is the harm finding.
- Per-channel deltas are **descriptive, not causal**. Channel is chosen by the
  ladder, not randomised, so those rows say where the money showed up, never what
  caused it.

---

## Data model

Nine tables ([`db/models.py`](db/models.py)), documented column by column in
[docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md).

`customers` · `signals` · `cases` · `diagnoses` · `playbooks` · `actions` ·
`outcomes` · `experiment_assignments` · `case_events`

Two properties built in from the first commit rather than bolted on:

- **`case_events` is hash-chained.** Each row's SHA-256 covers its own content
  *plus* the previous row's hash, so editing or deleting any past event breaks
  every hash after it. `verify_case_chain` re-walks a case's ledger and names the
  first bad row; the replay page renders the result as a VERIFIED / BROKEN stamp.
- **Every LLM call is logged on its row** — model, prompt template id, input hash,
  raw output, latency. Given a `case_id`, the full decision path is reconstructable
  from the database alone.

Money is stored in **paise** everywhere as integers, and formatted exactly once in
`eval/money.py`, which both the CLI report and the dashboard's Jinja filter call —
so a rupee figure can never disagree between them.

---

## Dashboard

FastAPI + Jinja2 + one hand-written stylesheet. **No React, no Tailwind, no CSS
framework, no npm, no build step.**

- Filtering, sorting and pagination are server-side query params, so every view is
  a linkable URL that renders with JavaScript disabled.
- **All SVG geometry is computed in Python** ([`dashboard/viz.py`](dashboard/viz.py)) —
  the Sankey ribbons, the confidence-interval plot, the veto distribution bar, the
  run charts. No charting library. 20 tests assert the arithmetic: bands sum to
  their parents, one uniform rupees-per-pixel across the whole diagram, the
  incremental callout anchored to the band it measures.
- 79 lines of JavaScript total, for three things: the scoreboard count-up, the
  flow-diagram reveal, and the kill switch's typed confirmation. All three are
  enhancements — the page is complete and every control works without them.

The scoreboard's hero subdivides the treated arm's recovered band at the reported
incremental figure, so the highlighted strip is literally the money attributed to
treatment, sitting directly against the holdout's recovered band. Band thickness
stays proportional to rupees throughout and the bands still sum exactly.

---

## Extending it — the handover test

> Adding a new revenue lane should mean writing **one playbook YAML** and **one
> signal adapter**, with zero changes to the orchestrator, the policy engine, or
> the measurement code.

That is the test the architecture is built to pass. `decide` reads ladders from
data; `diagnose` reads its rules table from data; the gate, the state machine,
the holdout assignment and the report are all lane-agnostic. See
[docs/PLAYBOOK_AUTHORING.md](docs/PLAYBOOK_AUTHORING.md).

---

## Tests

```bash
make test     # 264 tests
```

| File | Covers |
|---|---|
| `test_policy.py` (33) | Each of the ten bounds, passing and failing |
| `test_report.py` (34) | Report arithmetic against hand-computed answers |
| `test_dashboard.py` (36) | Every route, JS-disabled rendering, kill switch, run-page concurrency |
| `test_viz.py` (20) | SVG geometry — proportionality, summation, clamping |
| `test_groundtruth.py` (14) | The prediction, and estimator convergence at scale |
| `test_orchestrator.py` (14) | State machine, promise-to-pay, veto classification |
| `test_assignment.py` (13) | Stratification, idempotency, reproducibility |
| `test_live_run.py` (12) | Stepping day-by-day == the batch runner, exactly |
| `test_metrics.py` (11) | Confidence interval against hand-computed values |
| plus | outcomes, diagnosis, decide, actions, killswitch, render |

Roughly 5,600 lines of source and 2,900 lines of tests.

---

## Documentation

| Doc | Contents |
|---|---|
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Kill switch on page one, all commands, a judge walkthrough, triage table |
| [docs/POLICY.md](docs/POLICY.md) | Every bound, every threshold and where it lives, the LLM boundary |
| [docs/DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) | All nine tables, column by column |
| [docs/PLAYBOOK_AUTHORING.md](docs/PLAYBOOK_AUTHORING.md) | Writing a lane, and what the loader enforces |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | The pitch-video script |

---

## Stack

Python · FastAPI · SQLAlchemy · SQLite · Pydantic · Jinja2 · PyYAML · pytest.
Nine dependencies, no build step, no external services. Everything runs offline.
