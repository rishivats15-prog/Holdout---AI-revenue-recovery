# Data dictionary

Nine tables, SQLite, defined in `db/models.py`. Legal values for the string
columns are in `db/enums.py`.

**Two conventions that apply everywhere:**

- **Money is stored in paise**, always, as integers. It is formatted exactly once,
  in `eval/money.py`, which both the CLI report and the dashboard's Jinja filter
  call. Never store or compute in rupees.
- **Timestamps are naive UTC.** SQLite cannot round-trip a timezone-aware
  datetime, so a fresh aware value and a queried naive one would crash on
  comparison. `db/models.py:utcnow()` is the only function that should ever call
  `datetime.now()`.

---

## `customers`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `external_ref` | str unique | The id a real gateway would use |
| `name`, `phone`, `email` | str | Synthetic |
| `dnd` | bool | Do-not-disturb. Read by the consent check → `CUSTOMER_DND` |
| `whatsapp_opt_in` | bool | Read by the consent check → `WHATSAPP_NOT_OPTED_IN` |
| `created_at` | datetime | |

## `signals` — raw, immutable

One row per inbound event, exactly as the source adapter normalised it. **Never
updated after insert.**

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `customer_id` | FK | |
| `case_id` | FK, nullable | Set by detect once the signal is folded into a case |
| `lane` | str | `subscription` \| `b2b` |
| `source` | str | `payment_webhook` \| `abandoned_cart` \| `overdue_invoice` |
| `failure_code` | str, nullable | `NULL` means genuinely ambiguous free text — this is what routes a signal to the LLM fallback |
| `exposure_amount` | int | Paise |
| `raw_payload` | JSON | The event as received. Carries `_ground_truth_cause`, a field no real gateway sends |
| `occurred_at` | datetime | When it happened at the source |
| `received_at` | datetime | When this system saw it |

## `cases`

The unit of work. Retries within one open episode collapse into **one** case.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `customer_id` | FK | |
| `lane` | str | |
| `state` | str | See the state machine below |
| `exposure_amount` | int | Paise. The max across folded signals |
| `detected_at` | datetime | **Anchors the 14-day attribution window** |
| `ladder_rung` | int | Next rung to propose |
| `paused_until` | datetime, nullable | Promised date + the playbook's grace window |
| `true_root_cause` | str, nullable | **Ground truth.** Readable only by `sim/outcomes.py` and `eval/groundtruth.py`. Diagnose, decide, and policy code must never read it |
| `created_at`, `updated_at` | datetime | |

**States:** `detected` → `diagnosed` → `in_treatment` → one of `recovered`,
`written_off`, `human_queue`, `suppressed`. Plus `paused` — non-terminal, entered
on a promise-to-pay; a broken promise re-enters `in_treatment` one rung higher.

## `diagnoses`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `case_id` | FK | The latest row wins if a case is re-diagnosed |
| `cause` | str | One of `RootCause`. Never a free-form label |
| `confidence` | float | `1.0` for rules, lower for the LLM fallback |
| `evidence` | JSON | Rule path: `failure_code`, `retry_viable`, `rule_description`. LLM path: `model`, `prompt_template_id`, `input_hash`, `raw_output`, `latency_ms` |
| `method` | str | `rule` \| `llm_fallback` |
| `created_at` | datetime | |

## `playbooks`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `lane`, `name`, `version` | str | Unique on (lane, version) |
| `source_path` | str | The YAML this was loaded from |
| `content` | JSON | The parsed YAML, snapshotted at load |
| `is_active` | bool | |
| `loaded_at` | datetime | |

## `actions`

One row per proposal that reached the gate — **including every vetoed one**. A
vetoed row is a first-class record, not an error log.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `case_id`, `playbook_id` | FK | |
| `ladder_rung` | int | |
| `proposed_action` | str | The action type from the playbook rung |
| `channel` | str, nullable | `retry` \| `sms` \| `whatsapp` \| `voice` \| `email` |
| `policy_verdict` | str | `allowed` \| `vetoed` |
| `veto_reason_code` | str, nullable | An uppercase constant from `policy/reasons.py` |
| `executed_action` | str, nullable | `NULL` when vetoed — nothing was sent |
| `cost` | int | Paise. Always `0` for a vetoed action |
| `delivered` | bool, nullable | **Distinct from the verdict.** An allowed action can still fail to reach the customer. `NULL` when vetoed |
| `idempotency_key` | str unique | `case:{id}:playbook:{id}:rung:{n}:tick:{n}` — re-running the same attempt in the same tick returns the existing row instead of sending twice |
| `external_id` | str, nullable | What the (fake) channel returned |
| `llm_model`, `llm_prompt_template_id`, `llm_input_hash`, `llm_raw_output`, `llm_latency_ms` | | Populated only when a model was involved. Unused until copy generation is wired |
| `created_at` | datetime | |

## `outcomes`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `case_id` | FK | |
| `action_id` | FK, nullable | The touch this followed, where there was one |
| `outcome_type` | str | `payment` \| `ptp` \| `dispute` |
| `amount` | int, nullable | Paise, for payments |
| `promised_date` | datetime, nullable | For promises-to-pay |
| `occurred_at` | datetime | **A payment outside `detected_at + 14 days` is never counted as a recovery**, whatever the case row says |
| `created_at` | datetime | |

## `experiment_assignments`

Written once per case, right after diagnosis and **before any treatment**, so an
arm can never be influenced by what happens to the case afterwards.

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `case_id` | FK unique | Exactly one assignment per case |
| `arm` | str | `treatment` \| `holdout` |
| `stratum` | str | `{diagnosed cause}:{exposure band}` — e.g. `balance_timing:mid`. Bands: low `<₹300`, mid `₹300–1,200`, high `≥₹1,200` |
| `seed` | int | Deterministic per case. `sim/outcomes.py` draws that case's recovery plan from it |
| `assigned_at` | datetime | |

Stratification uses the **diagnosed** cause, not `true_root_cause` — a real
experiment can only stratify on what it can observe.

## `case_events` — append-only, hash-chained

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `case_id` | FK | |
| `event_type` | str | See below |
| `payload` | JSON, nullable | |
| `prev_hash` | str(64) | The previous event's hash for this case; `"0"*64` for the first |
| `hash` | str(64) unique | SHA-256 over this row's content **plus** `prev_hash` |
| `created_at` | datetime | |

**Rows are never updated or deleted.** Because each hash covers the previous one,
editing or removing any past event breaks every hash after it.
`db/hashchain.py:verify_case_chain` re-walks a case's ledger and names the first
bad row; the replay page shows the result as a `VERIFIED` / `BROKEN` stamp.

**Event types:** `case_detected`, `signal_folded`, `case_diagnosed`,
`action_proposed`, `action_executed`, `action_vetoed`, `case_routed`,
`case_paused`, `promise_broken`, `rung_skipped`, `case_recovered`,
`case_written_off`, `case_suppressed`, `case_disputed`.

Given a `case_id`, the full decision path — signal in, diagnosis with its
provenance, each proposal, the gate's verdict and the bounds it evaluated, the
outcome — is reconstructable from these tables alone, with no code reading
required.
