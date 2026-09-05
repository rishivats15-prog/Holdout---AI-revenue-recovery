# Authoring a playbook

A playbook is one YAML file per revenue lane, at `playbooks/<lane>.yaml`. It holds
the ordered treatment ladder for every root cause that lane can diagnose.

## The handover test

> Adding a new revenue lane should mean writing **one playbook YAML** and **one
> signal adapter**, with zero changes to the orchestrator, the policy engine, or
> the measurement code.

That is the architectural claim this file exists to keep honest. `decide/decide.py`
never branches on a cause by name — it only ever asks the playbook "what is rung
N for this cause?". If you find yourself needing to edit a Python file to add a
lane, something has leaked out of the data and into the engine.

## File shape

```yaml
lane: subscription
name: subscription_default
version: "1"

promise_to_pay:
  grace_days: 3          # how long past a promised date before it counts as broken

ladders:
  balance_timing:
    - rung: 0
      action_type: silent_smart_retry
      channel: retry             # an outbound/automated treatment
      cooldown_minutes: 60
      cost_paise: 150
      copy_template: null
    - rung: 1
      action_type: sms_pre_debit_notice
      channel: sms
      cooldown_minutes: 1440
      cost_paise: 20
      copy_template: sms_balance_timing_v1
    - rung: 2
      action_type: human_agent_handoff
      channel: null
      terminal_state: human_queue   # hands the case straight to a state
      cooldown_minutes: 0
      cost_paise: 0
      copy_template: null
```

## Rules the loader enforces

`decide/ladder.py` validates at load time and fails loudly rather than at runtime:

- **Every rung has exactly one of `channel` or `terminal_state`** — never both,
  never neither. A rung either reaches out or routes the case out.
- **Rungs are numbered from 0 and contiguous.**
- **The playbook's causes must exactly match the lane's diagnosis causes.** A
  cause with no ladder, or a ladder for a cause the diagnosis table cannot
  produce, is an error. This is what stops a lane from silently half-existing.
- **`copy_template` ids referenced on SMS rungs** are checked against
  `policy/dlt_templates.yaml` by the consent check at gate time.

## Writing a good ladder

Order rungs cheapest-and-quietest first. The subscription lane's shape is:

```
silent smart retry → SMS → WhatsApp with an alternate instrument → Hinglish IVR → human
```

Two principles worth copying:

1. **Start at the fix, not at a retry.** `stale_credential` and `mandate_dead`
   ladders begin with a card-update or re-mandate link, because retrying a dead
   credential is pure burn. A ladder that always opens with a retry is a ladder
   that hasn't read its own diagnosis table.
2. **Some causes get no outreach at all.** `risk_fraud` is a single rung with
   `terminal_state: human_queue` and no channel. Suppressing dunning is a valid
   treatment plan.

`cost_paise` is a per-attempt channel cost. Decide only attaches it to the
proposal; the policy gate's cost-ceiling and EV-floor checks are what read it.

## Adding a lane, end to end

1. **`diagnosis/<lane>.yaml`** — the failure-code → root-cause table for the
   lane, plus a `safe_fallback_cause`. Causes must already exist in
   `db/enums.py:RootCause`.
2. **`playbooks/<lane>.yaml`** — one ladder per cause in that table.
3. **`ingest/adapters/<source>.py`** — normalise the lane's raw events into the
   `signals` shape. One adapter per source.
4. Nothing else. Detect, diagnose, decide, the gate, the orchestrator, the
   holdout assignment, and the eval report are all lane-agnostic.

The B2B lane is the intended proof of this: missing PO/GRN mismatch → send the
document rather than chase money; goods dispute → route to a human and never dun;
wrong AP contact → re-route; genuine cash-flow stress → negotiate a promise-to-pay
schedule. It should require no engine change at all.

## Viewing what's loaded

`/playbooks` on the dashboard renders the active playbook and diagnosis table
straight from the YAML, alongside the ten policy bounds every rung passes through.
It is the fastest way to check that an edit landed the way you intended.
