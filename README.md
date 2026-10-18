# analytics_poc: Conversational BI, V0.1 Data Foundation

This is the deterministic data foundation for a conversational BI app. The source CSVs live in S3. They are exposed through
Athena as raw (all-string) external tables, with typed, PII-filtered views on top, and a draft
semantic layer. Later phases will add a governed semantic layer, Bedrock and Streamlit. **They are not part of this phase.**

**Status:** scripts and instructions are prepared. **Nothing has been deployed.**

## Layout

```
infrastructure/  setup_athena.md (deploy steps), iam_requirements.md, config/*.json, policies/*.json
sql/             create_database.sql + GENERATED raw tables, typed views, validation
semantic/        tables.yaml (source of truth), metrics.yaml (draft metrics)
scripts/         catalog.py, generate_sql.py, discover_s3.py, verify_athena.py
tests/           test_metadata.py (offline, no AWS)
```

## Quick start

```powershell
& "C:\Users\RamPrasad\AppData\Local\Python\bin\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\discover_s3.py          # metadata only
.\.venv\Scripts\python.exe scripts\verify_athena.py        # read-only checks
```

The deployment steps are in [infrastructure/setup_athena.md](infrastructure/setup_athena.md).

## Dataset inventory (`s3://opta-s3-demo/nbfc-base-data/`, ap-south-1)

| Table | Source file | Size | Columns | In typed view | Line endings |
|---|---|---|---|---|---|
| account_summary | AccountSummary/FactAccountSummary.csv | 28.7 MiB | 91 | 91 | LF |
| dim_adm | DimADM/DimADM.csv | 1.1 MiB | 68 | 63 | CRLF |
| dim_calendar | DimCalendar/DimCalendar.csv | 1.9 MiB | 62 | 62 | CRLF |
| dim_customers | DimCustomers/DimCustomerDetails.csv | 3.0 MiB | 161 | 29 | LF |
| dim_par_bucket | DimPARBucket/DimPARBucket.csv | 28 KiB | 5 | 5 | CRLF |

All files are comma-delimited with double-quoted fields and no byte-order mark. Each file has one header row.

## Schema approach
- **Raw tables** (`raw_*`): OpenCSVSerde, every column `string`, header skipped. Column order follows the CSV header exactly.
- **Typed views** (`v_*`): trimmed values, with `''`/`'NULL'` turned into NULL. Conversions use `try_cast` and `try(date_parse)`, so a failed conversion becomes NULL rather than an error.
  Dates are parsed as `yyyy-mm-dd`, which matches 100% of date values in the profiled files. Flags map Y/YES/TRUE/T/1 and N/NO/FALSE/F/0 to boolean.
  CRLF files have the trailing carriage return stripped from the last column.
- **PII**: each column is classed `none`, `identifier`, `quasi`, `sensitive` or `direct`. Only `none` and `identifier` columns reach the views.
  `dim_customers` works as an allow-list.
- **Relationships:** none are declared. Candidate grains are listed as UNVERIFIED.

## Profiling results (`scripts/profile_s3.py`, aggregate-only, non-PII columns only)
- Rows: account_summary 27,973 · dim_adm 1,200 · dim_calendar 3,653 · dim_customers 1,000 · dim_par_bucket 731. There are no malformed rows and no embedded newlines.
- Every typed column converts with 0 failures. All dates are ISO.
- **This is synthetic demo data.** 52 columns hold a single `"Demo <Column>"` placeholder, 15 are all zero, and 14 are constant.
  All of these are tagged with `quality:` in `tables.yaml` and must not be used in metrics.
- Resolved questions:
  - `interest_rate` is a fraction (0.16–0.25).
  - The PAR columns are outstanding amounts.
  - `ticket_size` is a bucket label.
  - `date_key` is yyyymmdd.
  - The fiscal year starts on 1 April and is named by its end year.
  - `dim_adm` is a single 2026-03-31 snapshot.
- Still open: repayment columns contain negative values, `size_category` duplicates `ticket_size`, tenure units are assumed to be months, and `transaction_date` cadence is unconfirmed.

## Validation plan (`sql/validate_data.sql`, 27 aggregate-only checks)

| Check | What it tells us | Pass criteria |
|---|---|---|
| `row_structure` | Row count, and rows whose last column is empty | Empty last column ≈ 0. A high count means embedded newlines or broken quoting |
| `conversion_failures` | For each typed column, non-empty raw values that failed to convert | 0 for every column. If not, fix the type or format in `tables.yaml` |
| `date_shapes` | Date value shapes with digits masked (`9999-99-99`) | One ISO shape per column. Any other shape means `date_formats` must be extended (needs review if it's dd/mm vs mm/dd) |
| `flag_domains` | The distinct raw values of each flag column | All values map to true or false |
| `typed_profile` | Min/max dates, negative amounts, sums, distinct counts, nulls | Date ranges are plausible, negatives are explained |
| `grain_uniqueness` | Duplicate groups on the candidate grain | 0 duplicates, or a documented reason |

## Milestone 2: governed query service (`bi_service/`)

`QuerySpec` (metric, dimensions, filters, period, sort, limit) → validate → compile to SQL (allowlisted, single SELECT on
`v_account_summary`) → Athena (workgroup `analytics_poc_wg`) → `QueryResult`. The service has no Streamlit or Bedrock
dependency and has no input path for raw SQL.

```powershell
.\.venv\Scripts\python.exe scripts\run_query.py --list
.\.venv\Scripts\python.exe scripts\run_query.py --example q1_outstanding_by_state
.\.venv\Scripts\python.exe scripts\run_query.py --compile-only --example q6_quarterly_outstanding_fy2026
.\.venv\Scripts\python.exe scripts\run_query.py --file my_spec.json
.\.venv\Scripts\python.exe -m pytest -q             # offline (Athena is mocked)
.\.venv\Scripts\python.exe -m pytest -m live -q     # live golden comparison (scans about 0.5 GB in total)
```

```python
from bi_service import QueryService
svc = QueryService.from_defaults()
r = svc.run({"metric": "gross_npa_ratio", "dimensions": ["product_code"]})
```

- **Snapshot metrics** (semi-additive: balances, counts, PAR, NPA) resolve to the latest month-end snapshot in the period,
  or the last snapshot per time bucket. They are never summed across months.
- **Period metrics** (additive: disbursement, demand, repayment) sum every month in the period, and a period is required.
- **Ratios** are `SUM(num) / NULLIF(SUM(den), 0)`. A zero denominator gives a null value and a `zero_denominator` warning.
- **Semantic definitions** are in `semantic/metrics.yaml`, `dimensions.yaml`, `relationships.yaml` (all disabled) and `examples.yaml`.
  The plan and decisions are in [docs/milestones/milestone_2_plan.md](docs/milestones/milestone_2_plan.md).

## Required approvals before deploy
See [infrastructure/setup_athena.md](infrastructure/setup_athena.md) and the open decisions in the project hand-off.
