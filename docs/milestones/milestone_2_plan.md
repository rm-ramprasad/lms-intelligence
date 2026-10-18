# Milestone 2 — Plan (for approval)

Status: **IMPLEMENTED 2026-10-18.** The plan was approved with these decisions: month-end snapshots; balances semi-additive; flows additive;
K2–K9 defaults accepted; single view. 119 offline tests pass. The live golden check passes 7/7 against hand-written SQL.
Outstanding: the runtime IAM policy (`infrastructure/policies/analytics-query-runtime-policy.json`) still has to be created by an admin.

## Phase A — Discovery results

Source: `scripts/discover_athena.py` → `docs/metadata_inventory.json` (Glue catalog metadata only, no data read).

| Item | Value |
|---|---|
| Identity | `arn:aws:iam::791896160716:user/analytics-dev-user`, ap-south-1 |
| Database / workgroup | `analytics_poc` / `analytics_poc_wg` (enforced, 1 GiB cutoff, SSE-S3 results) |
| View definitions | Retrieved for all 5 views; each reads only its own `raw_*` table |

| View | Cols | Dates | Measures | Dimensions | Flags | Identifiers |
|---|---|---|---|---|---|---|
| v_account_summary | 91 | 3 | 53 | 22 (+5 numeric) | 4 | account_number, customer_id |
| v_dim_adm | 68 | 9 | 6 | 40 | 4 | account_number, customer_id |
| v_dim_calendar | 62 | 23 | 0 | 8 (+15 numeric) | 4 | — |
| v_dim_customers | 161 | 3 | 1 | 154 (mostly PII) | 0 | customer_id, original_customer_id |
| v_dim_par_bucket | 5 | 0 | 0 | 2 (+1 numeric) | 0 | — |

**Candidate join keys** (shared column names only; **none are approved**):
`account_number` (summary↔adm), `customer_id` (summary↔adm↔customers), `transaction_date` (summary↔adm),
`transaction_date`→`dim_calendar.calendar_date`, `par_bucket`/`loan_dpd_days`→`dim_par_bucket`,
`branch_code` / `product_code` / `state_code` (shared attributes, not true keys).

**What can actually be measured.** Every metric candidate lives in `v_account_summary`. 52 columns elsewhere are mock placeholders,
and `dim_adm` is a single 2026-03-31 snapshot. So V0.1 can be **single-view** (`v_account_summary`) with no joins at all.

## Proposed semantic model

Design rules:
1. **Allowlist only.** The compiler can reference only views, columns, aggregations and dimensions declared in YAML.
2. **No user SQL.** Input is a `QuerySpec`. SQL is assembled from fixed templates plus allowlisted identifiers plus validated, typed literals.
3. **Aggregates only.** Identifiers (`account_number`, `customer_id`) and PII columns can never be grouping dimensions or returned values. They may only be counted.
4. **Status gate.** Only metrics with `status: approved` can be executed. `pending_approval` metrics are rejected, unless a dev-only flag is set in tests.

| File | Content |
|---|---|
| `semantic/tables.yaml` | Existing physical catalog plus `query_access: governed / blocked` per view. Only `v_account_summary` is governed in V0.1 |
| `semantic/metrics.yaml` | Restructured per milestone. Fields: id, name, view, aggregation (`sum` / `count_distinct` / `ratio{numerator,denominator}`), behavior (`snapshot` / `period_flow`), required filters, allowed dimensions, type/format, definition, `status`, `validation_sql` |
| `semantic/dimensions.yaml` | id, view, column, label, data type, `sort_by` (e.g. par_bucket → par_bucket_order), allowed filter operators, `groupable` / `filterable` |
| `semantic/relationships.yaml` | The candidate joins above, all `status: pending_approval`, `enabled: false`. The compiler ignores them in V0.1 |
| `semantic/examples.yaml` | Business question → QuerySpec → expected SQL → independently verified result (golden set) |

**Time handling (no calendar join needed):**
- Time grain on `transaction_date`: `day / month / quarter / year / fiscal_year`.
  - Month, quarter and year use `date_trunc`.
  - Fiscal year uses `year(date_add('month', 9, d))`, i.e. April start, named by end year. That rule was confirmed from `dim_calendar` profiling.
- **Snapshot metrics** (balances, counts): the query must resolve to exactly one snapshot date.
  - `as_of` = a specific date, or a period end, which resolves to the latest `transaction_date` in that period.
  - Summing across dates is impossible: when a time grain is requested, the compiler takes the last snapshot in each bucket.
- **Period-flow metrics** (disbursement, demand, repayment): summed across the dates in the period. *The exact semantics depend on ambiguity K3.*
- **Ratios** are always `SUM(num) / NULLIF(SUM(den), 0)`, computed at the requested grain and never averaged. A zero denominator returns `NULL` plus a `zero_denominator` warning in the result.

**Draft metric list** (all `pending_approval` until you confirm them):

| ID | Aggregation | Behavior | Status / blocker |
|---|---|---|---|
| total_outstanding | SUM(total_os) | snapshot | K2 |
| account_count | COUNT(DISTINCT account_number) | snapshot | K9 |
| active_account_count | COUNT DISTINCT where ac_status='Active' | snapshot | K9 |
| sanctioned_amount | SUM(sanctioned_amount) | snapshot | — |
| disbursement_amount | SUM(disbursement_amount) | period_flow | K3 |
| demand_amount | SUM(demand_amount) | period_flow | K3 |
| repayment_amount | SUM(repayment_amount) | period_flow | K3, K7 |
| collection_efficiency | ratio | period_flow | K4 |
| par_30_plus_amount | SUM(par_31_to_60 + par_61_to_90 + par_over_90) | snapshot | K5 |
| par_30_plus_ratio | ratio over total_os | snapshot | K5 |
| npa_outstanding | SUM(total_os) where npa_flag | snapshot | K6 |
| gross_npa_ratio | ratio over total_os | snapshot | K6 |

**Draft dimensions** (non-placeholder, non-PII, from `v_account_summary`): loan_group, product_code, branch_code,
state_name, state_code, ac_status, nature, par_bucket, ticket_size, npa_flag, write_off_flag, restructure_flag,
plus the time grains on transaction_date.

## KPI ambiguities (need your decision)

| # | Question | Why it matters |
|---|---|---|
| K1 | What is the cadence of `transaction_date` (daily, month-end)? I can find out with **one aggregate query** (count of rows per date, about 29 MB scan) if you approve | It drives snapshot resolution and flow summing |
| K2 | Is outstanding = `total_os` or `ledger_balance`? (Their profiles are identical) | Official balance definition |
| K3 | Are disbursement/demand/repayment on each row **flows for that date or month**, or cumulative/MTD? | Summing MTD values across days overcounts |
| K4 | Collection efficiency = `repayment_for_current_month_demand / demand_amount`? Should overdue collections or prepayments be included? | Business definition |
| K5 | PAR 30+ = (31–60 + 61–90 + 90+) / total_os? Should PAR 1+ and PAR 90+ also be metrics? | Bucket boundaries |
| K6 | Is NPA defined by `npa_flag`, `par_over_90`, or `loan_dpd_days > 90`? | Regulatory definition |
| K7 | Negative repayment values: are they reversals that should be netted (default) or excluded? | Totals |
| K8 | Fiscal year starts in April and is named by end year (FY 2026 = Apr 2025–Mar 2026): confirm as the reporting default | Period labels |
| K9 | "Active account" = `ac_status = 'Active'`? | Count definition |
| K10 | Should V0.1 stay single-view with no joins? (Recommended: no relationship is needed for the first questions) | Scope |

## Five target business questions (acceptance #1)

1. What is total outstanding by state as of the latest snapshot?
2. How many active accounts are there in each loan group as of 2026-03-31?
3. What is the monthly disbursement trend for FY 2026?
4. What is the PAR 30+ ratio by branch as of the latest snapshot?
5. What are NPA outstanding and the gross NPA ratio by product as of the latest snapshot?
6. (stretch) What is monthly collection efficiency for FY 2026?

Each one gets an entry in `examples.yaml` with a hand-written, independently verified SQL query and its recorded result.
A live test compares the service output with it (acceptance #3).

## Proposed file structure

```
bi_service/                 # importable package, no Streamlit/Bedrock imports (enforced by test)
  __init__.py
  semantic_loader.py        # load + cross-validate YAML into frozen models; fails fast on bad config
  query_models.py           # QuerySpec, Filter, DatePeriod, Sort, QueryResult (pydantic v2, JSON schema for later Bedrock tool use)
  query_validator.py        # metric/dimension/filter/operator/value/limit/status checks → typed errors
  sql_compiler.py           # templates + allowlisted quoted identifiers + typed literal builder; single SELECT only
  athena_executor.py        # start/poll/timeout→stop, workgroup pinned, MaxResults cap, bytes-scanned capture
  query_service.py          # validate → compile → guard → execute → QueryResult; logs metadata only
  config.py                 # profile/region/workgroup/limits from tables.yaml aws block + env overrides
semantic/  tables.yaml metrics.yaml dimensions.yaml relationships.yaml examples.yaml
infrastructure/policies/analytics-query-runtime-policy.json   # read-only runtime policy (for approval)
scripts/  discover_athena.py (done)  run_query.py (CLI: QuerySpec JSON → result)  verify_examples.py (live golden check)
tests/    test_semantic_loader.py test_validator.py test_compiler.py test_executor.py test_service.py
          test_injection.py test_examples_live.py (marked `live`, skipped by default)
```

## Security controls (Phase D)

| Requirement | Control |
|---|---|
| Approved views only | The compiler's FROM clause comes only from `tables.yaml` views with `query_access: governed`. A post-compile guard re-checks it |
| No DDL/DML or multi-statement SQL | One template-built `SELECT`. The guard rejects `;`, comments and any non-SELECT keyword. No raw SQL input path exists |
| Dimension/filter permissions | `dimensions.yaml` `groupable` / `filterable` / operators. Identifiers and PII are never allowed |
| Safe literals | Values are type-checked: date → `DATE 'YYYY-MM-DD'` from a parsed `date`; numbers → `Decimal` repr; strings → length ≤ 100, printable, `'` doubled; `IN` lists ≤ 50 |
| Row limits | Default 100, maximum 1000. `LIMIT n+1` detects truncation, and `GetQueryResults` MaxResults is capped |
| Workgroup / scan / timeout | Workgroup pinned in code (scan cap enforced server-side), 60 s default timeout → `StopQueryExecution`, result status `timeout` |
| IAM | A new read-only runtime policy (Athena in `analytics_poc_wg`; Glue Get* on `analytics_poc`; S3 Get on the source; results-bucket write). **Needs your approval to create** |
| Logs | Only query_id, metric, dimensions, timing, bytes scanned and row count are logged. Rows, literal values and SQL text are never logged at INFO level |

## Testing (Phase E)

Unit tests mock boto3 with `botocore.stub.Stubber`. They cover every case in the milestone: valid queries, invalid metric,
unauthorized dimension, invalid filter, snapshot-date handling, additive vs semi-additive, ratio maths, zero denominator,
injection payloads (in values, dimension names and sort keys), Athena FAILED state, empty results, timeout plus cancellation.
Live golden tests run only with `pytest -m live`, which needs approval each time.

## Implementation order (after approval)

1. Semantic YAML files plus loader with cross-validation tests.
2. Models plus validator plus tests.
3. Compiler plus injection tests, and a golden SQL snapshot per example.
4. Executor (mocked) plus service plus tests.
5. Runtime IAM policy file. You apply it.
6. Live run of the 5 example questions, **with your approval**. Compare against hand-written SQL and record the results.

Stop before Bedrock and Streamlit.
