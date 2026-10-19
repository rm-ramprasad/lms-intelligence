# Tier 2 — Investigate: explain why a KPI changed (DRAFT brief)

> **Superseded 2026-10-19:** Tier 2 is now the **Executive Playbook** (curated, pre-computed questions); see
> [tier2_plan.md](tier2_plan.md). The analysis toolkit below is reused by the playbook engine.

Product tiers:
1. **Ask:** governed answers to "what is X?" (built: Milestones 1–4).
2. **Investigate:** "why did X change?" (this brief).
3. **Act:** recommendations and alerts from findings (later).
4. **Predict:** forecasts and early warning (later).

First prototype scope (approved 2026-10-19): **PAR 90+, gross NPA ratio, collection efficiency** on the demo data.

## Objective

When a user asks "why did PAR 90+ increase?", "what drove the drop in collection efficiency?" or
"why is Tamil Nadu's GNPA high?", return an **evidence-based investigation**:
- what changed, by how much, between which periods
- where it changed (which segments contributed)
- how it changed (mix vs rate, flows between overdue buckets, entries and exits)
- the caveats: what the data cannot tell us

Every number comes from governed, deterministic computation. The AI plans which analyses to run and narrates
the findings. It never computes numbers or invents causes.

## Principles

- **Contributors, not causes.** The language is "X contributed 62% of the increase", never "X caused it".
  External causes (economy, policy, borrower behaviour) are listed as *questions to check*, not conclusions.
- **Governed analysis templates.** Each analysis is an approved, tested SQL template, like Tier 1 metrics.
  No free-form SQL from users or the LLM.
- **Aggregates out, accounts in.** Analyses may join account-level rows *inside Athena* (e.g. month-over-month bucket
  movement), but only aggregates leave Athena. No account identifiers are ever shown or sent to the LLM.
  Concentration is reported as counts ("12 accounts explain 60% of the increase"), never as ids.
- **Materiality thresholds.** Only contributors above a set share of the change are reported. Everything else is
  grouped as "other".
- **Reproducible.** The same question and data give the same investigation. Narration has a numeric faithfulness check.

## Analysis toolkit (deterministic)

| # | Analysis | Answers | Applies to |
|---|---|---|---|
| A1 | **Change summary** | Value in period A vs B, absolute and % change | All KPIs |
| A2 | **Segment contribution** | Which state / branch / product / loan group / ticket size drove the change (Δ by segment, share of total Δ) | Additive and semi-additive KPIs |
| A3 | **Mix vs rate decomposition** | For ratios: did the ratio move because segments got worse (rate effect) or because riskier segments grew (mix effect)? | GNPA ratio, PAR ratios, collection efficiency |
| A4 | **Bucket roll-rate matrix** | Account and amount flows between DPD buckets month over month (Current→1-30→…→90+, cures back) | PAR, NPA |
| A5 | **Bridge / waterfall** | Opening → inflows (roll-ins, new NPAs) → outflows (cures, closures, write-offs) → balance changes → closing | PAR 90+, NPA outstanding, total outstanding |
| A6 | **Numerator vs denominator** | Did the ratio rise because the problem grew, or because the book shrank? | All ratios |
| A7 | **Concentration** | How many accounts explain most of the change (counts only) | Amount KPIs |
| A8 | **Trend context** | Is this month's move unusual against the last 12 months (z-score / range)? | All KPIs |

A4, A5 and A7 need month-over-month **account-level joins inside Athena** (`account_number` + `transaction_date`).
Prerequisite: verify the grain (one row per account per month-end), which was marked UNVERIFIED in Task 1.

## Investigation flow

```
"why did PAR 90+ increase?"
  1. Resolve:  KPI + comparison periods (default: latest month vs previous month; "this quarter vs last"; "vs last year")
               + scope filters ("in Tamil Nadu"). Uses the Tier 1 interpreter and catalog.
  2. Plan:     pick analyses from the KPI's approved investigation playbook (investigations.yaml).
               Deterministic by default; optionally Nova chooses the order and focus from the playbook only.
  3. Compute:  run the governed analysis templates (parallel Athena queries, aggregates only).
  4. Rank:     score findings by materiality (share of Δ, statistical unusualness).
  5. Explain:  template narrative + optional AI narrative (faithfulness-checked), plus caveats and follow-ups.
  6. Drill:    every finding has "investigate further" buttons (e.g. "Tamil Nadu by branch").
```

**Playbook example** (`semantic/investigations.yaml`, pending approval):
```yaml
par_90_plus_amount:
  default_comparison: previous_month
  steps: [A1, A6, A5, A4, A2: [state_name, branch_code, loan_group, ticket_size], A7, A8]
  caveats: ["Cannot attribute to borrower circumstances or macro factors", "Write-offs reduce PAR 90+ mechanically"]
```

## Output (the "Investigation" view)

1. **Headline:** "PAR 90+ rose ₹4.2 Cr (+11%) from Feb to Mar 2026."
2. **Waterfall:** opening, roll-ins from 61-90, new entries, cures, write-offs, closures, closing.
3. **Top contributors table:** segment, Δ, share of Δ, rate vs mix effect.
4. **Roll-rate heatmap:** bucket-to-bucket movement this month vs the 12-month average.
5. **"What stands out":** 3–5 ranked findings in plain language.
6. **"What the data can't tell you":** caveats and suggested business questions.
7. **Drill-down buttons, and a "How this was calculated" panel** for every number.

## Prerequisites and new governed items (need approval)

- **New KPIs:** `par_90_plus_amount`, `par_90_plus_ratio`, possibly `par_60_plus_*` and `cure_rate`, `roll_rate_61_90_to_90_plus`.
- **Grain check:** one aggregate query confirming uniqueness of (`account_number`, `transaction_date`).
- **Definitions for flows:** what counts as a "cure", a "new NPA", a "write-off exit", a "closure exit" (business definitions).
- **Comparison periods:** default comparison per KPI (month-on-month for balances, period-on-period for flows).
- **Materiality thresholds:** e.g. report contributors ≥ 10% of Δ, maximum 5 per dimension.

## Architecture

```
bi_service      (Tier 1, unchanged)        QuerySpec → governed SQL → QueryResult
bi_investigate  (new, depends on bi_service)
   comparison.py      ComparisonSpec (metric, period A/B, scope filters)
   analyses/          A1..A8, each = governed SQL template + Python post-processing (pure functions, unit-tested)
   playbooks.py       loads investigations.yaml; validates against metrics/dimensions
   engine.py          plan → run (parallel, cached) → rank → InvestigationResult
   narrator.py        template text + optional Nova narration (faithfulness-checked)
bi_chat         routing: "why / what drove / explain the change" → Investigate (replaces today's refusal)
app             new "Investigate" view: waterfall, contributors, roll-rate heatmap, findings, drill-downs
```

## Testing and acceptance (proposed)

- Each analysis is unit-tested against a small synthetic dataset with known answers. Example: a known mix shift
  must come out as a mix effect.
- **Reconciliation:** waterfall components sum exactly to the closing balance; segment Δs sum to the total Δ;
  mix and rate effects sum to the ratio Δ.
- **Golden investigations** (5–8), with expected top contributors checked by hand-written SQL.
- No account identifiers in any output, log or prompt (tested).
- Investigation completes in ≤ 15 s (parallel queries, cached), at a cost of cents.

## Out of scope for Tier 2

Causal inference beyond decomposition, external data (macro, bureau), forecasting, recommendations or actions,
account-level drill-through (names or ids).
