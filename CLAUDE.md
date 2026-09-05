# AI Revenue Recovery

## What you're building

An **AI Revenue Recovery** system for failed payments — subscriptions,
invoices, abandoned checkouts — for a Razorpay internship screening test.
This is a fixed-deadline hackathon-style submission judged by Razorpay, not a real client deliverable. There is no access to a real payment gateway sandbox, so the entire system runs on synthetic data end to end.
That's a deliberate design choice, not a limitation to apologize for: it means the measurement layer can be validated against a known ground
truth, which a live gateway wouldn't allow.

**Stack:** Python, FastAPI, SQLite (file-based — zero setup, fine for this scale), Pydantic for schemas, Jinja2 + hand-written CSS for the dashboard — no frontend framework, no build step, no npm. See **Dashboard design** below for the full spec. Keep dependencies boring and few.

## Design principle — read this before writing any code

> The LLM proposes, a deterministic policy engine disposes, and a
> holdout group decides whether any of it worked. Nothing that touches
> money, compliance, or the scoreboard runs through a model.

Concretely: LLM calls are allowed in exactly two places — (1) classifying ambiguous free-text signals into a fixed diagnosis enum, and (2) natural-
language copy generation for outbound messages. Every other decision —
what action to take, whether it's allowed, how to measure results — is
plain rules and arithmetic, unit-testable, and has nothing to do with a
model's output at runtime. If you find yourself about to let an LLM
decide an action or compute a metric, stop — that's a policy-engine or
eval job instead.

## Architecture — six layers

1. **Signals in** — normalize every failure source (payment webhook,
   abandoned cart, overdue invoice) into one `signals` event shape before
   anything else touches it. One adapter per source.
2. **Detect** — pure rules, no LLM. Turns a signal into a `case`
   (`exposure_amount`, `detected_at`, `lane`, `customer_id`). Dedupe hard
   here — one customer's three failed retries must be one case, not
   three.
3. **Diagnose** — map the failure to a root cause using the diagnosis
   table below. Rules cover clean failure codes; LLM fallback only for
   genuinely ambiguous free text, constrained to output one of the
   existing enum values, never a new one.
4. **Decide** — look up the ordered treatment plan (the "ladder") for
   this root cause and case state from the playbook. Rules-first.
5. **Execute** — channel adapters (fake, since synthetic) behind a
   policy gate that can veto any action. Idempotency keys on every
   attempt.
6. **Measure** — compare treated cases against a randomized, stratified
   holdout. This produces the headline number.

## Case state machine

States: `detected` → `diagnosed` → `in_treatment` → one of
`recovered` (money in) / `written_off` (EV below cost) /
`human_queue` (dispute or risk) / `suppressed` (opt-out or cap).

Add a fifth, non-terminal state: `paused`. A promise-to-pay freezes the
case until the promised date plus a grace window; breaking the promise
re-enters treatment one rung higher on the ladder rather than starting
over. Build this as a natural consequence of the state machine, not a
bolted-on special case.

## Diagnosis table — subscription lane (build this first, as data)

Encode as a YAML/JSON table, not as if/else code, so it's auditable and
extensible without touching the diagnosis engine:

| Failure signal | Root cause | Retry viable? | Correct intervention |
|---|---|---|---|
| Insufficient funds | Balance timing | Yes, timed | Retry at salary date / T+1 morning; pre-debit notice |
| Card expired or invalid | Stale credential | Not until fixed | Card update link; offer UPI Autopay switch |
| Issuer soft decline | Limit or issuer risk | Yes, different route | Retry via alternate acquirer, different time of day |
| Mandate revoked / not found | Mandate is dead | Never | Re-mandate flow only — retries here are pure burn |
| Debit exceeds mandate cap | Mandate ceiling | No | Amend mandate or split the debit |
| Gateway timeout | Transient | Yes, immediately | Auto-retry in minutes under an idempotency key |
| Risk or fraud decline | Risk | Never | Route to risk queue, suppress all dunning |
| Account closed or frozen | Dead instrument | No | Collect an alternate instrument |

Add a second, smaller table for the B2B lane (missing PO/GRN mismatch →
send the doc, don't chase money; goods dispute → route to human, never
dun; wrong AP contact → re-route; genuine cash-flow stress → negotiate a
PTP schedule). Build B2B as a second playbook YAML only — it should
require zero changes to the engine. That's the test of whether the
architecture actually generalizes.

## Data model — 9 tables

`customers` · `signals` (raw, immutable) · `cases` (exposure, lane,
state) · `diagnoses` (cause, confidence, evidence, method) ·
`playbooks` · `actions` (proposed action, policy verdict, executed
action, channel, cost, idempotency key, external id) · `outcomes`
(payments, PTPs, disputes) · `experiment_assignments` (arm, stratum,
seed) · `case_events` (append-only ledger).

Two things to build in from day one, not bolt on later:
- **Hash-chain `case_events`**: each row includes a hash of its own
  content plus the previous row's hash. About 20 lines, and it's what
  makes the audit trail tamper-evident rather than just a log table.
- **Every LLM call logged on its `actions` row**: model name, prompt
  template id, input hash, raw output, latency. Given a `case_id`,
  someone should be able to reconstruct the full decision path from the
  database alone, no code reading required.

## Policy gate — make every bound explicit and enforced in code

- Action allowlist — agent selects from playbook-declared actions only,
  cannot invent one
- Quiet hours — no contact outside 8am–7pm
- Frequency caps — max touches per case, per channel per day, cooldown
  between escalation rungs
- Consent/channel eligibility — DND status, WhatsApp opt-in, DLT SMS
  template compliance (model these as flags even though synthetic)
- Financial authority — max discount/waiver without human sign-off
- Cost ceiling — cumulative attempt cost capped as % of exposure
- Expected-value floor — stop when `p(recover) × exposure < cost of
  next touch`
- Content rules — no legal threats, no third-party disclosure
- Circuit breaker — if batch-wide failure rate spikes, halt all
  outbound action

Log every veto with a reason code on the `actions` row. A vetoed action
with a clean reason code is as important a demo screen as a successful
recovery — it's the proof the gate is real and not decorative.

## Measurement — this is where the submission is actually won or lost

Randomly assign 10–20% of synthetic cases to a holdout (business-as-
usual / nothing), **stratified by exposure band and root cause** so the
arms are comparable. Report:

- Incremental recovery rate = treated rate − control rate
- Incremental rupees = that delta × treated case count × mean exposure
- Cost to recover per ₹100, net recovered after channel/gateway costs
- Median days-to-cash, touches per successful recovery
- Harm metrics: opt-out rate, complaint rate, policy-veto rate —
  treated vs. control
- Per-arm lift by root cause and channel
- A two-proportion confidence interval on the incremental delta

Fix the attribution window at 14 days from detection; never count a
payment outside it. The synthetic generator must bake in a **known**
ground-truth natural-recovery baseline per root cause, so the measured
incremental lift can be checked against the true simulated effect — this
is what makes the CI defensible instead of decorative.

## Repo structure

```
/playbooks       one YAML per lane — thresholds, ladder, caps, copy
/diagnosis       rules table + LLM fallback classifier
/policy          deterministic gate, versioned, unit-tested
/actions         channel adapters: sms, whatsapp, voice, retry, email (all fake/logging)
/orchestrator    state machine runner, scheduler, circuit breaker
/sim             synthetic batch generator + event replay
/eval            holdout analysis, batch report generator
/dashboard       case list, replay view, scoreboard (FastAPI + Jinja2)
/docs            RUNBOOK.md, POLICY.md, PLAYBOOK_AUTHORING.md, DATA_DICTIONARY.md
```

`RUNBOOK.md` must document the kill switch on page one: the single
command to halt all outbound action, and who's allowed to run it.

The handover test to keep yourself honest: adding a new revenue lane
should mean writing one playbook YAML + one signal adapter, with **zero**
changes to the orchestrator, policy engine, or measurement code.

---

## Dashboard design

The measurement layer is what wins the submission; the dashboard is how a
judge *sees* that it's honest. It gets built to the same standard as the
policy engine — but under the same dependency discipline as everything
else here. Jinja2 templates plus one hand-written stylesheet.

### Stack

- Jinja2 templates in `/dashboard/templates/`, static assets in
  `/dashboard/static/`
- One stylesheet, `static/css/app.css`, with tokens in
  `static/css/tokens.css`
- Fonts via Google Fonts `<link>`
- **No React, no Tailwind, no CSS framework, no npm.** If a task seems to
  need a build step, it doesn't.
- Filtering and sorting are server-side via FastAPI query params, not
  client JS
- Vanilla JS only for: the scoreboard count-up, the flow diagram path
  draw, and the kill-switch confirmation. Under ~100 lines total, in one
  file.
- **Charts are hand-written SVG.** Geometry is computed in Python in
  `/eval` or a `/dashboard/viz.py` helper and passed to the template as
  coordinates. No charting library. This keeps the arithmetic in the same
  place as the rest of the measurement code, where it's testable.

### What it must say up front

Synthetic data with a known ground-truth baseline is a strength of this
submission, not a caveat. The status bar carries
`SYNTHETIC · GROUND TRUTH KNOWN` on every page, and the scoreboard shows
measured incremental lift beside the true simulated effect. A judge
should see the measurement validating itself without being told to look.

### Tokens

`/dashboard/static/css/tokens.css`. No template or stylesheet references
a hex directly.

```css
--ground:        #EFE7D8;   /* page */
--surface:       #F7F2E7;   /* raised panels, rail */
--surface-high:  #FCF9F2;   /* overlays */

--rule:          #DCD0B9;   /* hairlines, column grid */
--rule-strong:   #C6B69B;   /* table header underline, active borders */

--ink:           #17140F;   /* primary */
--ink-2:         #5C5344;   /* secondary */
--ink-3:         #8E8371;   /* muted — all mono utility labels */

--accent:        #B65A2E;   /* terracotta — interactive, emphasis */
--accent-soft:   #E8CDB6;   /* tint fills, hover, selected rows */

--recovered:     #5F7145;   /* moss — money in */
--recovered-soft:#DCE0C9;
--veto:          #8A2E22;   /* oxblood — policy blocked */
--veto-soft:     #EBD3CC;
--at-risk:       #B8862F;   /* ochre — exposure, pending, paused */
--at-risk-soft:  #EEDCB8;
```

`--accent` and `--veto` are close by design. Colour is never the only
signal separating an allowed action from a vetoed one — vetoed elements
also carry a structural cue (a break in the timeline spine, a hatched
fill, an uppercase reason code). Everything stays legible in greyscale.

Light mode only. No dark theme, no toggle.

### Type

Four roles, Google Fonts:

- **Bodoni Moda** — display. Page titles and the single hero figure on
  the scoreboard. Roughly six appearances total. Never below 32px.
- **Newsreader** — body serif. Prose only: descriptions, empty states,
  evidence lists. Never tabular data.
- **Instrument Sans** — interface. Buttons, navigation, form labels,
  table column headers.
- **IBM Plex Mono** — data. Every rupee amount, rate, percentage, count,
  timestamp, case ID, reason code, policy version, model name. Tabular
  numerals always (`font-variant-numeric: tabular-nums`). Uppercase and
  letterspaced ~0.12em as a small label; unspaced as a value.

The mono is the workhorse. A number in any other face is a bug.

Sentence case everywhere except mono utility labels and reason codes,
which are uppercase constants.

### Layout devices

**Column grid.** A faint four-column vertical grid in `--rule` at low
opacity behind every page, aligned to the content grid. Barely
perceptible, like a ruled sheet. The only permitted decoration.

**Status bar.** A mono line in `--ink-3`, letterspaced, between two
hairlines above the content on every page. Contents are contextual per
route; the synthetic marker and clock are always present.

```
N° BATCH / 0417    WINDOW 14D    ARM SPLIT 85/15    POLICY v2.3    SYNTHETIC · GROUND TRUTH KNOWN    18:11
```

### Pages

Required:

| Route | Page |
|---|---|
| `/` | Scoreboard |
| `/cases/{case_id}` | Case replay |
| `/vetoes` | Veto log |

Build if time allows, in this order: `/cases` (queue), `/playbooks`,
`/batches/{batch_id}`.

**Scoreboard.** The hero is not a stat grid — it's the flow of money, as
a horizontal Sankey in hand-written SVG. Exposure at risk splits into
treated and holdout; each splits into recovered and not. Band thickness
is proportional to rupees, computed in Python. The two recovered bands
sit adjacent so the eye reads the gap between them — that gap is the
product. Label the difference directly on the diagram in display serif as
the incremental figure. Paths draw left to right on mount over ~900ms.

Below it, one row of figures separated by vertical hairlines, values in
mono, labels in letterspaced mono at `--ink-3`: incremental recovered
(the only figure in display serif, with its confidence interval in small
mono beneath, and the known ground-truth effect beside it for
comparison), gross recovered at deliberately lower weight, cost per ₹100,
net recovered, median days to cash, veto rate. Harm metrics — opt-out and
complaint rate, treated vs holdout — get equal visual weight to the money
metrics.

**Case replay.** A vertical spine in `--rule-strong` down the left,
rendered straight from `case_events`. Each event is a node: signal
received, case opened, diagnosis assigned, then one node per proposed
action showing the proposal (channel, rung, `rule` or `llm_fallback`),
the policy verdict, and the outcome.

The critical detail: a vetoed action renders as **a gate the case visibly
did not pass through**. The spine breaks, a hatched block in
`--veto-soft` sits across it, the reason code sits in uppercase mono in
`--veto`, and the rules evaluated expand on click. Allowed actions flow
through unbroken. Someone should read this top to bottom and see exactly
where the agent was stopped and why, without a legend.

Right column: customer and exposure, diagnosis with evidence and
confidence, the playbook and current rung, promise-to-pay block when
present, and the `case_events` hash chain with a verified/broken
indicator. LLM-proposed steps show their logged model metadata inline in
small mono; rule-proposed steps show the rule id. Provenance is never
ambiguous.

**Veto log.** Reason code, case, blocked action, timestamp — a dense
table, filterable by reason code via query param. A horizontal
distribution bar showing which codes fire most, in `--veto` at varying
opacity. This page exists to prove the gate is real, so it should look
substantial, not like an error log.

### Chrome

A slim left rail in `--surface` with the page links, a live
circuit-breaker indicator, and the kill switch at its base. The kill
switch POSTs to the halt endpoint and requires typing `HALT` to confirm;
once engaged, a persistent band in `--veto-soft` sits across the top of
every page and the rail indicator changes state. It reflects the same
flag the CLI kill switch sets — the two must never disagree.

### Conventions

- Money is stored in paise and formatted only in a Jinja filter. Indian
  digit grouping (₹12,45,890). Never round in a way that hides the
  number.
- Tables: compact rows, hairline separators, no zebra striping, no
  card-per-row. Generous horizontal padding, tight vertical. Row state is
  a narrow leading vertical bar in the semantic colour; full badges only
  in the state column.
- Radius 3px on controls, 4px on panels. Nothing pill-shaped except
  status badges.
- One elevation level. Shadows only on overlays, warm-tinted not grey.
- Empty states are directions, not moods: state what matched nothing and
  offer the action that fixes it. No illustrations.
- Copy is active voice, sentence case, user-side vocabulary.
- Motion is functional only: the count-up runs once on mount (~600ms,
  ease-out), the flow diagram draws once. Hover and focus are instant.
  `prefers-reduced-motion` disables both.

### Never produce

Gradient heroes, glassmorphism, grids of equal rounded cards, emoji as
icons, stock illustrations, "welcome back" greetings, centred marketing
copy, decorative dividers, or 01/02/03 numbering on anything that isn't a
genuinely ordered sequence. Playbook rungs are ordered; nothing else is.

### Quality bar

- Rupee figures use tabular numerals and Indian grouping; columns align.
- Incremental recovery outweighs gross recovery visually wherever both
  appear.
- A vetoed case's replay page is legible to someone who has never seen
  the product, without a legend.
- Vetoed and allowed actions stay distinguishable in greyscale.
- The status bar appears on every page with contextually correct
  contents.
- Every page renders correctly with JavaScript disabled, except the two
  animations.
- Keyboard navigable, focus rings visible in `--accent`.
- Responsive down to 1024px. Print styles drop the rail and column grid,
  keep the hairlines.

---

## Build phases — work through these in order, stop and show me the result at the end of each one before continuing

### Phase 0 — Scaffold
FastAPI project skeleton, SQLite + SQLAlchemy models for all 9 tables,
Pydantic schemas, empty module directories per the repo structure above,
`pyproject.toml`/`requirements.txt`, a `Makefile` or simple scripts for
`run`, `seed`, `test`. Basic `RUNBOOK.md` stub with the kill-switch
section first, even if the kill switch itself isn't wired yet.
**Done when:** `uvicorn` boots, empty DB migrates cleanly, `pytest`
collects zero tests without error.

### Phase 1 — Signals, Detect, synthetic generator
Build `/sim` to generate synthetic subscription-failure signals with a
realistic root-cause distribution and a known per-cause natural-recovery
baseline. Build the `signals` adapter and the `detect` step (dedup +
case creation) for the subscription lane only.
**Done when:** running the seed script populates `signals` and `cases`
tables with 800–1000 realistic, deduplicated cases.

### Phase 2 — Diagnose
Encode the subscription diagnosis table as YAML/data. Build the rules
engine that maps signal → root cause. Stub the LLM fallback path
(interface only, can hardcode a response for now) constrained to the
existing enum.
**Done when:** every seeded case gets a `diagnoses` row with a cause and
a method (`rule` or `llm_fallback`), and a unit test proves the LLM path
can never emit an out-of-enum value.

### Phase 3 — Playbook + Decide
Write the subscription playbook YAML (ladder: silent smart-retry → SMS →
WhatsApp with alternate instrument → Hinglish IVR → human agent).
Build the `decide` step that looks up the ordered treatment plan given
cause + case state.
**Done when:** every diagnosed case produces a proposed next action from
the playbook, with no hardcoded logic outside the YAML.

### Phase 4 — Policy gate + Execute
Implement every bound listed above as a discrete, independently unit-
tested check. Build fake channel adapters (log the "send" with a payload
that looks like a real API call, but hits nothing external). Wire
idempotency keys.
**Done when:** a batch run produces a mix of executed and vetoed
actions, each vetoed one carrying a specific reason code, and each check
has its own passing/failing test cases.

### Phase 5 — Orchestrator + paused state
Wire the full state machine end to end, including `paused` for promise-
to-pay with re-entry one rung higher on broken promises. Add the
circuit breaker (batch-wide failure spike halts outbound action).
**Done when:** a case can be traced through every state including a
promise-to-pay pause and break, purely by querying `case_events`.

### Phase 6 — Experiment assignment + Eval
Implement stratified holdout assignment (by exposure band and root
cause). Build the `/eval` batch report: incremental recovery rate,
incremental rupees, cost per ₹100, days-to-cash, harm metrics, per-arm
lift by cause/channel, and the two-proportion confidence interval.
**Done when:** running the eval script against a completed batch
produces a report whose measured incremental lift is close to the known
synthetic ground-truth effect — sanity-check this explicitly, it's your
proof the measurement is honest.

### Phase 7 — Dashboard + docs + demo polish
Build the three required pages to the **Dashboard design** section above
— that section is the spec, follow it exactly rather than restating a
simpler version of it. Compute all SVG geometry in Python. Finish
`RUNBOOK.md`, `POLICY.md`, `PLAYBOOK_AUTHORING.md`,
`DATA_DICTIONARY.md`.
**Done when:** you can run one command, seed a fresh batch, and walk
someone through all three pages without touching code, and the dashboard
meets every item in the Dashboard design quality bar.

---

## Working agreement

- Work phase by phase, in order. After each phase, summarize what was
  built, show me how to run/verify it, and wait for a go-ahead before
  starting the next one.
- Prefer explicit, boring code over clever abstraction — every policy
  check and diagnosis rule should be independently readable and testable
  by someone who didn't write it.
- Nothing that decides an action, checks a policy bound, or computes a
  metric should ever call an LLM. Flag it loudly if you're about to
  cross that line.
- Everything is synthetic — never write code that assumes or silently
  depends on a real gateway being present.
