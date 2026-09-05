# Demo script — Holdout

One minute talking. One minute clicking, in silence. Roughly two minutes total.

## Before you record

```
make seed        # ~15s
make run         # http://127.0.0.1:2000
```

Full-width browser, 100% zoom. Open four tabs in this order: `/run`, `/`,
`/cases/320`, `/vetoes`. Left rail must say **RUNNING** — if it says HALTED, run
`make resume`.

---

# PART 1 — Speak (0:00 – 1:00)

No screen. Read this straight through. It's timed to land just under a minute.

> This is Holdout. It recovers failed subscription payments.
>
> When a payment fails, most systems retry it and send reminders. Some money comes
> back, but nobody can tell you how much of it would have come back anyway.
>
> So Holdout does three things. It diagnoses the root cause first — a revoked
> mandate needs a re-mandate link, not another retry. Every action then passes a
> policy gate with ten hard bounds, including a kill switch. And fifteen percent
> of cases are randomly held out and get nothing at all, so the headline number is
> incremental recovery, not gross.
>
> A model runs in exactly two places: reading ambiguous failure text, and writing
> copy. Nothing that picks an action or computes a number ever calls a model.
>
> The data is synthetic, so I know the true effect before I measure it — and I
> check my measurement against it. Here it is running.

---

# PART 2 — Click (1:00 – 2:05)

**Say nothing.** Just click and hold. The pause times are what let a viewer read
the screen — don't rush them.

| # | Do this | Then |
|---|---|---|
| 1 | On `/run`, click **New batch** | Pause **2s** |
| 2 | Click **Advance one day** | Pause **3s** — counters move |
| 3 | Click **Advance one day** again | Pause **3s** |
| 4 | Click **Advance five** | Pause **4s** — the charts start climbing |
| 5 | Left rail: click **Halt all outbound**, type `HALT`, submit | Pause **3s** — red band drops across the top |
| 6 | Click **Advance one day** | Pause **4s** — **vetoed jumps, recovered doesn't move** |
| 7 | Click **Resume outbound** | Pause **1s** |
| 8 | Click **Run to the end** | Pause **2s** |
| 9 | Rail → **Scoreboard**. Hold on the flow diagram | Pause **6s** |
| 10 | Scroll slowly to the figure row (₹65,974) | Pause **5s** |
| 11 | Scroll to the interval chart with the green line | Pause **5s** |
| 12 | Go to the `/cases/320` tab. Scroll to the three hatched red blocks | Pause **6s** |
| 13 | Click **Rules evaluated** to expand it | Pause **4s** |
| 14 | Scroll to the bottom of the timeline | Pause **4s** — ends on *Recovered* |
| 15 | Go to the `/vetoes` tab. Hold on the ten-bounds table | Pause **6s** |
| 16 | Scroll down to the veto log | Pause **4s**, then stop recording |

Total ≈ **62 seconds**.

## The three moments that carry it

If you're editing afterwards, these are the frames worth a caption or a zoom:

- **Step 6** — you halted the system mid-run, advanced a day, and nothing was
  sent. Everything got vetoed.
- **Step 10** — ₹2,17,634 recovered gross, but only **₹65,974** is credited to
  treatment. The rest would have arrived anyway.
- **Step 11** — the green line is the true simulated effect, and it falls inside
  the measured confidence interval.

## Numbers you'll see

| | |
|---|---|
| Exposure at risk | ₹8,60,936 |
| Gross recovered | ₹2,17,634 |
| **Incremental recovered** | **₹65,974** |
| Measured lift / true effect | 9.00% (CI 1.39–16.61%) / 11.88% |

**If something breaks:** port busy → `make run PORT=8000`. Everything vetoed →
`make resume`. `/run` shows a batch but no Advance buttons → click **New batch**.

**Don't click "Clear" on `/run` while preparing.** It empties every table, and
the dashboard will show zeros until you re-run `make seed`. **New batch** is the
one you want — it regenerates from the same seed, so the numbers above come back
identical.
