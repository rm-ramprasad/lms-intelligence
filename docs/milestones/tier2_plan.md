# Tier 2 — Executive Playbook: Plan (for approval)

Status: **Steps 1–5 implemented offline (2026-10-19); step 6 (live run) awaiting approval.** P1–P8 approved with defaults.
(Supersedes the earlier "ad-hoc investigate" plan.)

| Tier | Name | What it is |
|---|---|---|
| 1 | **Ask** (built) | Ad-hoc questions in plain language → governed answers |
| 2 | **Playbook** (this plan) | Curated executive questions, **pre-computed each month-end**, with answers, analysis and the "why", reviewed before release |
| 3 | Act (later) | Alerts and recommendations triggered by playbook findings |
| 4 | Predict (later) | Forecasts and early warning (uses Tier 2 roll-rate matrices) |

Audience: CFO and CXOs. Demo data (mock NBFC, 48 month-ends Apr 2022–Mar 2026).

---

## 1. Product concept

Executives open an **Executive Briefing** for a month-end and see:

1. **KPI tiles:** value, change vs last month/quarter/year, red/amber/green status.
2. **10 key questions:** each with a one-line answer, a chart, and an expandable **analysis** (the "why": waterfall,
   contributors, bucket movement, mix vs rate), plus "what the data can't tell you".
3. **Drill-down:** any finding opens Tier 1 Ask, pre-filled (e.g. "PAR 90+ in DEMO-SOUTH by loan group").
4. **History:** briefings for every month-end, so trends and earlier months can be reviewed. This also lets the demo
   show months with real movement; the latest month (Mar 2026) is quiet in the demo data.

Every number is pre-computed, reconciled and governed. Nothing is generated live in front of the executive.

## 2. The question bank (`semantic/executive_playbook.yaml`)

Phase 1 uses only **approved KPIs**, plus PAR 90+ (approved as T1).

| # | Theme | Question | KPIs | Analyses | Chart |
|---|---|---|---|---|---|
| Q1 | Growth | How big is the loan book and how did it move? | total_outstanding, active_account_count | change MoM/QoQ/YoY, segment contribution | KPI tile + 13-month line |
| Q2 | Growth | Where is growth coming from? | total_outstanding | segment contribution × state, product, loan group, ticket size | contribution bars |
| Q3 | Growth | What is the disbursement run-rate this fiscal year? | disbursement_amount | FYTD vs last FYTD, monthly trend, segment contribution | monthly bars |
| Q4 | Asset quality | What is the GNPA ratio and what drove it? | gross_npa_ratio, npa_outstanding | change, numerator vs denominator, NPA bridge, mix vs rate, contributors | waterfall |
| Q5 | Asset quality | What drove the change in PAR 90+? | par_90_plus_amount / ratio | PAR 90+ bridge (roll-ins by source bucket, new, cures, write-offs, closures), concentration | waterfall |
| Q6 | Early warning | Are accounts slipping into later buckets faster? | roll rates (from bucket movement) | roll-rate matrix vs 12-month average, unusualness | heatmap |
| Q7 | Early warning | Which branches and states are deteriorating fastest? | par_30_plus_ratio, gross_npa_ratio | change by segment, ranked, unusualness | ranked bars |
| Q8 | Collections | What is collection efficiency and where did it drop? | collection_efficiency | change, numerator vs denominator, mix vs rate, contributors | trend + bars |
| Q9 | Concentration | How concentrated is our risk? | total_outstanding, npa_outstanding | share of top states/branches; large-ticket share of NPA; concentration (counts) | stacked bars |
| Q10 | Summary | What should leadership look at this month? | all | top 3–5 findings across Q1–Q9, ranked by materiality and unusualness | list |

**Phase 2** (needs new KPI approvals): provision coverage ratio (provisions ÷ NPA, from the provision columns),
portfolio yield (outstanding-weighted interest rate), overdue-demand recovery rate.
**Not possible with this data:** profitability, cost of funds, plan/budget variance.

### Example entry

```yaml
- id: q5_par90_drivers
  theme: asset_quality
  question: What drove the change in PAR 90+?
  kpis: [par_90_plus_amount, par_90_plus_ratio]
  comparisons: [mom, qoq, yoy]            # mom shown by default; others in tabs
  analyses: [change, num_den, bridge, roll_rates, segments: [state_name, branch_code, loan_group], concentration]
  rag: {metric: par_90_plus_ratio, green_below: 0.02, amber_below: 0.04}   # PLACEHOLDER, business to set
  answer_template: "PAR 90+ {direction} {delta_amount} ({delta_pct}) to {value_b}; ratio {ratio_a} → {ratio_b}."
  caveats: ["Write-offs reduce PAR 90+ mechanically", "Cannot attribute to borrower circumstances or macro factors"]
  drilldowns: [{metric: par_90_plus_amount, dimensions: [branch_code]}, {metric: par_90_plus_amount, dimensions: [loan_group]}]
  owner: Credit risk
  status: pending_approval
```

## 3. Analysis engine (`bi_investigate/`): deterministic and reconciling

| # | Analysis | Method | Used by |
|---|---|---|---|
| A1 | Change | values at A and B, Δ, Δ% for MoM / QoQ / YoY | all |
| A2 | Segment contribution | Δ by segment, share of total Δ (Σ = total Δ) | Q1–Q4, Q7–Q9 |
| A3 | Mix vs rate | ΔR = Σ Δwᵢ·r̄ᵢ + Σ w̄ᵢ·Δrᵢ (midpoint, exact sum) | Q4, Q8 |
| A4 | Roll-rate matrix | bucket M-1 × bucket M (counts, amounts) vs 12-month average | Q5, Q6 |
| A5 | Bridge / waterfall | opening + roll-ins + new − cures − write-offs − closures ± balance change = closing | Q4 (NPA), Q5 |
| A6 | Numerator vs denominator | Δ ratio split into numerator and denominator effects | Q4, Q5, Q8 |
| A7 | Concentration | number of accounts explaining 50% / 80% of Δ (counts only) | Q5, Q9 |
| A8 | Unusualness | Δ vs the last 12 monthly Δs (z-score, rank) | all; drives Q10 |

Flow definitions (approved, T2): stayed, rolled forward, cured, new account, **closed = `ac_status = Closed` or absent
next month**, written off, new NPA, upgraded.

**Rules:**
- Every analysis must reconcile exactly, or it's withheld.
- Report contributors ≥ 10% of |Δ|, at most 5 per dimension (T4).
- No account identifiers anywhere.

## 4. Month-end briefing run

```
scripts/run_briefing.py --as-of 2026-03-31 [--all-history]
  1. For each question: build comparisons (MoM, QoQ, YoY) and run analyses (parallel Athena queries over Parquet, aggregates only)
  2. Reconcile all components; check RAG; rank findings (Q10)
  3. Render answer text from templates; optional Nova narrative (faithfulness-checked, labelled "AI summary")
  4. Save versioned snapshot: briefings/2026-03-31/briefing.json (+ query ids, prompt hash, playbook version)
  5. Status = DRAFT
scripts/approve_briefing.py --as-of 2026-03-31 --by "<name>"   →  status = APPROVED (executives see approved only)
```

- **History:** `--all-history` builds briefings for every month-end, so the demo can show months with real movement.
- **Storage (prototype):** local `briefings/` folder (git-ignored). **Later:** the curated S3 bucket under `briefings/`,
  which needs a small IAM change.
- **Cost:** about 10 questions × about 8 queries ≈ 80 Athena queries per month-end ≈ $0.004; all 48 months ≈ $0.20.
  The optional AI narrative is about $0.002 per briefing.

## 5. Executive Briefing page (Streamlit)

```
┌ Executive Briefing · 31 Mar 2026 · ✅ Approved by <name> on <date> ·  [month ▾]  [Export PDF] ┐
│ [Loan book ₹2,034 Cr ▲1.2% 🟢] [GNPA 2.62% ▲0.08pp 🟡] [PAR 90+ 2.05% ▲0.07pp 🟡] [CE 94.1% ▼1.3pp 🟠] │
│ What leadership should look at (Q10)                                                          │
│  1. PAR 90+ rose ₹4.2 Cr, 68% from accounts rolling in from 61-90 DPD       [Analysis ▸]      │
│  2. DEMO-SOUTH contributed 41% of the GNPA rise                              [Analysis ▸]      │
│  3. Collection efficiency fell in Rural Enterprise (rate-driven)             [Analysis ▸]      │
│ ── Growth ── Q1 · Q2 · Q3        ── Asset quality ── Q4 · Q5 · Q6 · Q7                        │
│ ── Collections ── Q8             ── Concentration ── Q9                                       │
│  each: answer line · chart · ▸ Analysis (waterfall / contributors / heatmap / mix-rate)       │
│        · What the data can't tell you · [Drill down in Ask] · ▸ How this was calculated       │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

Navigation becomes **Briefing** (default landing page) · Ask · KPI catalog · Guided builder.
(The example numbers above are illustrative.)

## 6. Testing and acceptance

1. **Synthetic tests:** each analysis is tested on hand-built data with a planted, known answer.
2. **Reconciliation:** bridge = closing − opening; Σ segment Δ = total Δ; mix + rate = Δ ratio (exact).
3. **Golden questions:** Q4, Q5, Q8 for 2 chosen months each, checked against hand-written SQL (live, approval needed).
4. **Snapshot integrity:** briefing JSON is schema-validated; every number traces to a query id; approved briefings are immutable.
5. **Privacy:** no account identifiers in snapshots, logs or prompts (tested).
6. **UI:** smoke test of the Briefing page from a fixture snapshot (no AWS).
7. **Performance:** one month-end briefing ≤ 2 minutes cold; the page loads instantly from the snapshot.

## 7. Implementation order

1. New KPIs `par_90_plus_amount` / `par_90_plus_ratio` (metrics, glossary, tests). **Offline.**
2. `bi_investigate`: models, comparisons, analyses A1–A8 with synthetic tests. **Offline.**
3. `executive_playbook.yaml` (Q1–Q10) plus loader and validation against metrics/dimensions. **Offline.**
4. Briefing runner, snapshot schema, approval script. Mocked Athena tests. **Offline.**
5. Executive Briefing page plus AppTest. **Offline (fixture snapshot).**
6. **Live:** one aggregate query to find the months with the biggest moves (demo selection); build briefings for all
   48 month-ends; golden checks (approval needed, about $0.20).
7. Demo script for the CFO walkthrough; README and CLAUDE.md updates.

Free-text "why" questions in Ask stay refused for now (a possible later add-on reusing the same engine).

## 8. Data check results (2026-10-19, `sql/checks/tier2_data_checks.sql`, 0.4 MB scanned)

| Check | Result |
|---|---|
| C1 grain | ✅ 27,973 rows = 27,973 distinct (account, month-end); 0 null accounts; 0 month gaps |
| C2 PAR 90+ | ✅ Σ par_over_90 = Σ total_os in the 90+ bucket in all 48 months |
| C3, all months | Roll-forward: Current→1-30 352, 1-30→31-60 120, 31-60→61-90 182, 61-90→90+ 174. Cures: 1-30→Current 230, 90+→Current 44. 1,155 new accounts, 653 exits (401 Closed, the rest absent), 50 write-offs, 176 new NPAs, 48 upgrades |
| C3, Mar 2026 | Quiet: about 500 accounts, 0 roll-ins to 90+, 0 new NPAs. Hence the history and demo month selection |

## 9. Decisions needed

| # | Decision | Proposed default |
|---|---|---|
| P1 | Question bank Q1–Q10 (phase 1) | As listed; add or drop any |
| P2 | Phase 2 KPIs (provision coverage, yield, overdue recovery) | Later, after phase 1 |
| P3 | Cadence and review | Monthly at month-end; DRAFT → APPROVED by a named reviewer; executives see approved briefings only |
| P4 | RAG thresholds | Placeholders in the YAML, flagged; business to set real values |
| P5 | History | Build briefings for all 48 month-ends (demo can pick months with movement) |
| P6 | Delivery | App page now; PDF export as a later add-on |
| P7 | Snapshot storage | Local `briefings/` for the prototype; S3 curated bucket later |
| P8 | Landing page | Briefing becomes the default page; Ask stays for ad-hoc questions |
