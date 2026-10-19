# Milestone 3 — Plan (for approval)

Status: **IMPLEMENTED 2026-10-19.** Decisions: D1 fiscal quarters; D2 fiscal YTD and last year; D3 ₹ Cr/₹ L;
D4 outstanding = loan book = book size = portfolio = AUM = POS (approved); D5 12 months; D6 SQL admin-only; D7 one KPI.
257 tests pass. The eval set has 64 cases plus 21 golden paraphrases, all passing. Changes from the plan: the period
vocabulary, stop words and refusal words live in `semantic/language.yaml`, and value lists are generated into
`semantic/dimension_values.yaml`. Brief: [milestone_3_conversational_interface.md](milestone_3_conversational_interface.md).

## 1. Repository review

| Asset | Reused as-is | Change needed |
|---|---|---|
| `bi_service` (QuerySpec → QueryResult) | Validator, compiler, executor | Phase B only: `include_total`, `snapshot_date`, display formatting, top-N already possible via sort+limit |
| `semantic/metrics.yaml` (12 approved) | Definitions, formulas, behavior | None. The glossary links by `metric_id` |
| `semantic/dimensions.yaml` (16) | Columns, operators, sort order | None |
| `semantic/examples.yaml` (7) | Golden questions | Add `paraphrases:` per example |
| `scripts/run_query.py` | CLI | Add `--ask "question"` for interpreter testing |

New packages: `bi_chat/` (interpreter, no Streamlit imports) and `app/` (Streamlit only). Dependency direction:
`app → bi_chat → bi_service`. Nothing imports upward, and a test enforces it.

## 2. Catalog structure (samples)

### `semantic/glossary.yaml`

```yaml
kpis:
  - metric_id: gross_npa_ratio
    name: Gross NPA ratio
    short: Share of the loan book that is non-performing.
    long: NPA outstanding (accounts flagged NPA) divided by total outstanding, at a month-end snapshot.
    formula: NPA outstanding ÷ Total outstanding
    synonyms: [gnpa, gross npa, npa ratio, npa %, npa percentage, gross npa ratio]
    unit: percent
    behavior_note: Month-end position. Compare months; do not add across months.
    related: [npa_outstanding, total_outstanding, par_30_plus_ratio]
    owner: Credit risk
    status: approved

  - metric_id: total_outstanding
    name: Total outstanding
    short: Principal and dues outstanding across all loan accounts.
    long: Sum of TotalOS across accounts at a month-end snapshot.
    formula: Σ TotalOS
    synonyms: [outstanding, total os, loan book, portfolio, aum, book size, pos]
    unit: inr_crore
    behavior_note: Month-end balance; cannot be added across months.
    owner: Finance
    status: pending_approval      # "aum", "pos" need sign-off; others approved separately if preferred

  - metric_id: disbursement_amount
    name: Disbursement
    short: Amount disbursed to borrowers in the period.
    formula: Σ DisbursementAmount over the months in the period
    synonyms: [disbursement, disbursements, disbursed, disbursal, loans given]
    unit: inr_crore
    behavior_note: Flow. Adds up across months; needs a period.
    owner: Business
    status: approved

dimensions:
  - {dimension_id: state_name, synonyms: [state, states, region, geography, statewise, state-wise]}
  - {dimension_id: branch_code, synonyms: [branch, branches, office, branch-wise]}
  - {dimension_id: par_bucket, synonyms: [par bucket, dpd bucket, overdue bucket, ageing, aging]}

terms:   # catalog page only, not used for matching metrics
  - {term: DPD, definition: Days past due on the oldest unpaid instalment.}
  - {term: Fiscal year, definition: "1 April to 31 March, named by end year (FY 2026 = Apr 2025 to Mar 2026)."}
```

### `semantic/value_aliases.yaml` (values from an aggregate `SELECT DISTINCT` refresh script)

```yaml
state_name:
  values: [Delhi, Gujarat, Karnataka, Madhya Pradesh, Maharashtra, Rajasthan, Tamil Nadu, Telangana, Uttar Pradesh, West Bengal]
  aliases: {tn: Tamil Nadu, mh: Maharashtra, up: Uttar Pradesh, wb: West Bengal, mp: Madhya Pradesh, ka: Karnataka,
            gj: Gujarat, rj: Rajasthan, tg: Telangana, dl: Delhi, bengal: West Bengal, ncr: Delhi}
loan_group:
  values: [Enterprise Flex, Growth Finance, Rural Enterprise, Working Capital Plus]
  aliases: {wc plus: Working Capital Plus, working capital: Working Capital Plus}
par_bucket:
  values: [Current, 1-30 DPD, 31-60 DPD, 61-90 DPD, 90+ DPD]
  aliases: {"90+": 90+ DPD, "over 90": 90+ DPD, "0 dpd": Current, regular: Current}
npa_flag:
  aliases: {npa accounts: true, non-npa: false, standard accounts: false}
```

The words "up", "mp" and "wb" collide with ordinary English ("up" as in "going up"). Rule: two-letter state codes only
match when written in UPPERCASE or after "in/for/of". The loader flags any alias that is also a stop-word.

### `semantic/period_phrases.yaml`

All relative phrases are anchored to **L = the latest snapshot date** (queried once per session, a `MAX` aggregate), not today.

| Phrase | Resolves to |
|---|---|
| latest, current, as of now, this month, today | `latest` |
| March 2026, Mar-26, Mar 26, 2026-03, march | `month` (a bare month name means the most recent such month ≤ L) |
| FY26, FY 2026, FY 2025-26, FY25-26 | `fiscal_year` 2026 |
| this fiscal year / FYTD / YTD | range Apr 1 of L's FY → L (YTD = FYTD, see decision D2) |
| last fiscal year, last year | previous fiscal year (D2) |
| Q4 FY26, Q1 | fiscal quarter range; a bare "Q1" means the most recent fiscal Q1 ≤ L (D1) |
| this quarter / last quarter | fiscal quarter containing L / the one before it |
| last 3 / 6 / 12 months | range covering N month-ends ending at L |
| between Jan 2026 and Mar 2026, from … to … | `range` |
| monthly, month-wise, trend, over time / quarterly / yearly | time dimension month / quarter / fiscal_year (a trend without a period → last 12 months) |

## 3. Interpreter decision rules

```
tokens  = normalise(question)                      # lowercase, strip punctuation, expand abbreviations
refuse  if identifier pattern (10+ digit number, PAN, "account number", "customer", phone/email) or
        forecast/why/predict words or a matched term that is pending/unapproved
matches = longest-phrase match: metrics, dimensions ("by/per/across/wise"), values, periods, top-N
leftover = tokens not matched and not stop-words

if metrics == 0                    → Clarify("Which KPI?", top 3 examples by overlap + KPI list)
if metrics > 1                     → Clarify("Which one?", the matched KPIs)      # V0.1: one KPI per question
if leftover                        → Clarify("I didn't recognise '<words>'", Did-you-mean examples)
if flow metric and no period       → Clarify("For which period?", [latest month, this FY, last FY])
if bi_service rejects the spec     → Clarify with the rejection message mapped to plain language
else                               → Interpreted(spec) → run → answer
```

Follow-ups (only when the previous turn was `Interpreted`):

| Utterance | Action |
|---|---|
| by / now by / split by <dim> | replace dimensions |
| also by <dim> | add a dimension |
| only / just / for <value> | add a filter |
| excluding / except <value> | add a `not_in` filter |
| remove filters / all states | clear filters |
| same for / what about <period> | replace period |
| what about <kpi> | replace metric, keep the rest |
| as a trend / monthly | add a time dimension |

## 4. UI wireframe

```
┌ Sidebar ─────────────┐┌ Chat ───────────────────────────────────────────────────────────────┐
│ ○ Ask                ││ You: gnpa by state for march                                         │
│ ○ KPI catalog        ││ ┌───────────────────────────────────────────────────────────────┐ │
│ ○ Guided builder     ││ │ Understood: [Gross NPA ratio ▾] by [State ▾]  period [Mar 2026 ▾]│ │
│                      ││ │ Gross NPA ratio was 2.62% at 31 Mar 2026 (₹53.34 Cr of           │ │
│ Data as of           ││ │ ₹2,034.10 Cr). Highest: Tamil Nadu 7.87%; 4 states at 0%.          │ │
│ 31 Mar 2026          ││ │ [ bar chart ]                                                     │ │
│                      ││ │ [ table: State | NPA ₹Cr | Outstanding ₹Cr | GNPA % ] [CSV]       │ │
│ Try:                 ││ │ ▸ How this was calculated   👍 👎                                   │ │
│ • Outstanding by     ││ └───────────────────────────────────────────────────────────────┘ │
│   state              ││ You: only TN and KA                                                  │
│ • Monthly            ││ …                                                                     │
│   disbursement FY26  ││ You: npa                                                              │
│ …                    ││ Bot: Did you mean:  [Gross NPA ratio]  [NPA outstanding]              │
└──────────────────────┘│ [ Ask about outstanding, NPA, PAR, disbursement…            ] [Send] │
                        └─────────────────────────────────────────────────────────────────────┘
```

**Catalog page:** search box, then KPI cards (name, definition, formula, unit, synonyms, behavior note, ▶ example).
Below that, the dimensions with their values.
**Guided builder:** dropdowns that produce the same `QuerySpec`, with a "Show as question" option that displays the canonical phrasing.

## 5. Evaluation set outline (`tests/eval/questions.yaml`, about 70 cases)

| Group | # | Example | Expected |
|---|---|---|---|
| Golden questions × 3 phrasings | 21 | "GNPA by product latest" | same spec as q5 |
| Every approved KPI via synonym | 12 | "loan book" | total_outstanding |
| Dimensions and values | 8 | "outstanding in TN by branch" | filter + dimension |
| Periods | 10 | "disbursement last quarter", "FY 2025-26" | resolved range |
| Top-N | 3 | "top 3 branches by PAR 30" | sort desc, limit 3 |
| Follow-ups | 6 | "now by loan group" | modified spec |
| Clarify | 6 | "npa", "show me numbers", "disbursement" (no period) | Clarify |
| Refuse | 6 | "outstanding for account 1234567890", "why did NPA rise", "forecast" | Refuse |

## 6. File structure

```
semantic/  glossary.yaml  value_aliases.yaml  period_phrases.yaml  examples.yaml (+paraphrases)
bi_service/  (Phase B: include_total, snapshot_date, formatting.py)
bi_chat/
  catalog.py            load + cross-validate glossary/aliases/periods against the semantic model
  normalizer.py
  period_parser.py
  matcher.py
  example_matcher.py
  interpreter.py        Interpreter protocol + DeterministicInterpreter → Interpreted | Clarify | Refuse
  conversation.py       follow-up state machine
  answer_renderer.py    template sentences from QueryResult
  audit.py              JSONL audit log (no result rows)
app/
  streamlit_app.py      pages: Ask, KPI catalog, Guided builder
scripts/  refresh_values.py (aggregate DISTINCT, approval needed)  build_catalog_doc.py → docs/catalog.md
tests/    test_catalog.py test_period_parser.py test_matcher.py test_interpreter.py test_conversation.py
          test_answer_renderer.py test_app_smoke.py  eval/questions.yaml + test_eval.py
```

## 7. Implementation order

1. Phase B service enhancements plus tests.
2. Catalog YAMLs, the loader with collision checks, and `refresh_values.py`. **One approved run** to pull distinct values.
3. Period parser, normaliser and matcher, test-first against the eval set.
4. Interpreter, conversation and answer renderer, until 100% of the eval set passes.
5. Streamlit app (Ask / Catalog / Builder) plus a smoke test, run locally.
6. `docs/catalog.md` generation, README and CLAUDE.md updates.
7. Live run of the golden paraphrases (approval needed). Stop before any shared deployment.

New dependency: `streamlit`. Charts use `st.bar_chart` / `st.line_chart`, so no extra charting library is needed.

## 8. Decisions needed

| # | Decision | Proposed default |
|---|---|---|
| D1 | Does "Q1" mean fiscal or calendar quarter? | **Fiscal** (Q1 = Apr–Jun), labels "Q1 FY26" |
| D2 | Do "YTD" and "last year" mean fiscal or calendar? | **Fiscal** (YTD = FYTD; last year = previous FY) |
| D3 | Display unit for amounts | **₹ Cr**, 2 decimals; ₹ L when the value < 1 Cr |
| D4 | Synonyms "AUM", "POS", "book" for total outstanding | Approve "loan book" / "portfolio"; leave **AUM and POS pending** (AUM may mean the daily-average AUM column) |
| D5 | Default for a trend question without a period | Last 12 month-ends |
| D6 | Show generated SQL in the UI | Admins only, via a sidebar toggle with an env flag |
| D7 | One KPI per question in V0.1 | Yes. Multi-KPI questions trigger a clarification |
