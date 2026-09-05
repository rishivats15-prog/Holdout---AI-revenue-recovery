# Policy

The policy gate is the deterministic layer between what the system *proposes* and
what it *does*. Nothing passes it without being checked, and nothing in it calls a
model.

## The design boundary

> The LLM proposes, a deterministic policy engine disposes, and a holdout group
> decides whether any of it worked.

An LLM runs in exactly two places in this system:

1. **`diagnosis/llm_fallback.py`** — classifying a genuinely ambiguous free-text
   failure reason into the existing root-cause enum. It is hard-constrained: the
   coercion to the enum happens in `coerce_to_enum`, not in the model call, so a
   model that invents a label cannot produce one.
2. **Outbound copy generation** — the sanctioned second site. The hook exists
   (`ProposedAction.copy_text`, and the `llm_*` columns on `actions`) and the
   content-rules check already scans whatever text is attached, but no copy model
   is wired in this submission.

Everything else — which action to take, whether it is allowed, how results are
measured — is plain rules and arithmetic. No policy check, no decide step, and no
metric ever calls a model.

## The ten bounds

Evaluated in this order by `policy/gate.py`. The first to object stops the send;
its reason code is written to the action row, along with the full list of checks
evaluated up to that point.

| # | Check | Bound | Reason codes |
|---|---|---|---|
| 1 | `kill_switch` | An operator can halt every outbound action with one command. | `KILL_SWITCH_ENGAGED` |
| 2 | `circuit_breaker` | Delivery failures above 30% (after ≥20 attempts) halt outbound action automatically. | `CIRCUIT_BREAKER_OPEN` |
| 3 | `action_allowlist` | Actions come from the lane's playbook. The agent cannot invent one. | `ACTION_NOT_IN_PLAYBOOK` |
| 4 | `quiet_hours` | No contact outside 8am–7pm. | `OUTSIDE_QUIET_HOURS` |
| 5 | `frequency_caps` | One touch per channel per day, plus the rung's declared cooldown between escalation steps. | `CHANNEL_FREQUENCY_CAP_EXCEEDED`, `RUNG_COOLDOWN_ACTIVE` |
| 6 | `consent_and_eligibility` | DND status, WhatsApp opt-in, and DLT SMS template registration. | `CUSTOMER_DND`, `WHATSAPP_NOT_OPTED_IN`, `SMS_TEMPLATE_NOT_DLT_APPROVED` |
| 7 | `financial_authority` | Waivers above ₹500 need human sign-off. | `FINANCIAL_AUTHORITY_EXCEEDED` |
| 8 | `cost_ceiling` | Cumulative spend on a case is capped at 8% of its exposure. | `COST_CEILING_EXCEEDED` |
| 9 | `expected_value_floor` | Stop when p(recover) × exposure falls below the next touch's cost. | `EXPECTED_VALUE_BELOW_COST` |
| 10 | `content_rules` | No legal threats, no third-party disclosure. | `CONTENT_POLICY_VIOLATION` |

Order matters only for *which* code gets logged when more than one bound would
fire. Global halts go first, then structural validity, then case-specific bounds.

## Where the numbers live

| Threshold | Value | File |
|---|---|---|
| Quiet hours | 08:00–19:00 | `policy/checks.py` |
| Touches per channel per day | 1 | `policy/checks.py` |
| Max waiver without sign-off | ₹500 | `policy/checks.py` |
| Cost ceiling | 8% of exposure | `policy/checks.py` |
| Circuit-breaker threshold | 30% failures, min 20 attempts | `policy/context.py` |
| Rung cooldowns | per rung | `playbooks/<lane>.yaml` |
| Recovery priors for the EV floor | per cause | `policy/recovery_priors.yaml` |
| DLT-approved template ids | per lane | `policy/dlt_templates.yaml` |
| Banned phrases | list | `policy/checks.py` |

The EV floor's `p(recover)` comes from `policy/recovery_priors.yaml` — a
business-side prior that decays with ladder rung. It is **not**
`sim/ground_truth.yaml`. The policy engine has no access to the simulator's truth,
and would not have it against a real gateway either.

## Versioning

`policy/gate.py:POLICY_VERSION` is stamped on every dashboard page and every
replayed action. Bump it when a check is added, removed, or a threshold changes,
so a vetoed action can always be traced to the ruleset that vetoed it.

## Auditing a veto

Every vetoed action writes a row to `actions` with:

- `policy_verdict = "vetoed"`, `veto_reason_code`, `cost = 0`, `delivered = NULL`
- a matching `action_vetoed` event on `case_events` carrying `checks_evaluated`,
  the ordered list of every bound checked before the one that fired

Which means: given a `case_id`, the full decision path is reconstructable from the
database alone. `/vetoes` lists them in aggregate; `/cases/{id}` renders one
case's path as a timeline.

## Testing

Each check is a plain function — `PolicyContext` in, a reason code or `None` out —
with its own passing and failing cases in `tests/test_policy.py`. A bound showing
zero fires on the veto log is a bound that had nothing to stop in that batch, not
an unimplemented one; the tests are what distinguish the two.
