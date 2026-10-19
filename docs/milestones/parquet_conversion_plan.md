# One-time Parquet conversion of account_summary (for approval)

Status: **DONE 2026-10-19.** Goal: faster queries. Incremental loads are out of scope (later).

## Result

- **Reconciliation:** 48/48 dates, 0 mismatches (rows, accounts, 6 measure sums). 27,973 rows on both sides.
- **Golden questions:** all 7 have identical result checksums on CSV and Parquet; the live comparison against reference SQL passes 7/7.
- **Measurements** (in `docs/benchmarks.jsonl`):

| | CSV | Parquet |
|---|---:|---:|
| Scanned (7 questions) | 361.3 MB | 0.19 MB |
| Total latency (7 questions) | 14.5 s | 12.2 s |
| Storage | 28.7 MB | 3.7 MB |

- **What remains in the latency:** Athena's fixed per-query overhead. Further gains need app-side caching or Athena result reuse.
- **Note on the switch:** right after the runtime policy update, 3 queries failed with S3 `AccessDenied` for about a minute
  (IAM propagation). A rerun succeeded.
- **Rollback (still available):** recreate `v_account_summary` as `SELECT * FROM analytics_poc.v_account_summary_staging`.

## Why

`v_account_summary` reads a 28.7 MB CSV in full, twice per snapshot query (~60 MB, ~2.2 s).
Columnar, compressed, month-partitioned Parquet should cut this to about 1–3 MB and roughly 1 s. These are estimates;
the before and after figures will be measured.

## Design

```
raw_account_summary (CSV, unchanged, source bucket)
   └─ v_account_summary_staging      typed view over raw (today's v_account_summary SQL, renamed)
        └─ CTAS (once) ──► account_summary_curated   Parquet + ZSTD, partitioned by transaction_date
                               s3://analytics-poc-curated-791896160716-ap-south-1/account_summary/
                                   └─ v_account_summary   SELECT <same 91 columns> FROM account_summary_curated
```

- **Consumers unchanged.** `v_account_summary` keeps the same name, columns and types, so `bi_service`, the catalog,
  the app and the tests don't change.
- **Partitioning.** By `transaction_date` (month-end dates), because every query filters or groups on it.
  Finding the latest date then needs no data scan. Before the CTAS, one aggregate query checks the number of distinct dates:
  a single CTAS can write at most 100 partitions, and more than that needs a CTAS plus INSERT batches.
- **Generated, not hand-written.** `tables.yaml` gets a `curated:` block for account_summary.
  `generate_sql.py` emits `sql/create_curated.sql` (staging view + CTAS) and repoints `v_account_summary`.
- **Source untouched.** Raw CSV, `raw_*` tables and the other four views don't change.

## AWS changes (admin, approval needed)

| # | Change | Why |
|---|---|---|
| 1 | New bucket `analytics-poc-curated-791896160716-ap-south-1`: SSE-S3, public access blocked, TLS-only, **no expiry**, versioning on | Durable home for Parquet; kept separate from the source bucket and the expiring results bucket |
| 2 | New workgroup `analytics_poc_etl_wg`: 1 GB scan cap, encrypted results, **not** enforcing the output location | Lets the CTAS write to the curated bucket (blocked under the enforced query workgroup). Only the dev user may use it |
| 3 | Dev policy (`analytics-dev-user`): allow `analytics_poc_etl_wg`; allow Put/Get/Delete/List on the curated bucket | Run the conversion and rebuild it if needed |
| 4 | Runtime policy (`analytics-query-runtime`): **add** Get/List on the curated bucket; **remove** read on the source `AccountSummary/` CSV | The runtime then reads only curated data, which tightens access |

I'll provide the JSON policy files and CLI/console steps. You (admin) apply them.

## Execution (dev profile, approval needed for each SQL run)

1. **Pre-check.** One aggregate query on raw: number of distinct `transaction_date` values, plus row count and
   Σ total_os per date. This is the baseline for reconciliation.
2. **Create the staging view.** Create `v_account_summary_staging`, identical SQL to today's view, in `analytics_poc_wg`.
3. **Convert.** Run the CTAS in `analytics_poc_etl_wg`: Parquet, ZSTD, `partitioned_by = ARRAY['transaction_date']`,
   `external_location` = the curated bucket.
4. **Reconcile.** Per date, compare row count, distinct accounts, and Σ of 6 key measures (total_os, disbursement, demand,
   repayment, PAR 30+, NPA outstanding) between staging and curated. Every one must match exactly. Aggregates only.
5. **Repoint.** `CREATE OR REPLACE VIEW v_account_summary` over the curated table.
6. **Verify.**
   - `verify_athena.py`
   - the full offline test suite
   - **the 7 golden questions live** (results must equal Milestone 2)
   - the latest-date lookup
7. **Measure.** Bytes scanned and latency for the 7 questions, CSV vs Parquet, recorded in this file.

**Rollback:** `CREATE OR REPLACE VIEW v_account_summary` back to the staging SQL. One statement, and the curated data stays in place.

## Admin steps

**Round A: before the conversion.**

1. **Create the curated bucket.** S3 → Create bucket `analytics-poc-curated-791896160716-ap-south-1` in ap-south-1.
   - ACLs disabled
   - block all public access
   - SSE-S3 with Bucket Key
   - **Versioning: Enable**
   - **no lifecycle rule**
   - Permissions → Bucket policy: paste `infrastructure/config/curated-bucket-policy.json`.
2. **Create the conversion workgroup.** Athena → Workgroups → Create `analytics_poc_etl_wg`.
   - Query result location: `s3://analytics-poc-athena-results-791896160716-ap-south-1/etl-results/`
   - Encrypt results: SSE-S3
   - **Override client-side settings: OFF**
   - Per-query data usage control: 1 GB
3. **Update the dev policy.** IAM → policy `analytics-poc-dev-policy` (on `analytics-user-group`) → Edit → JSON:
   replace it with `infrastructure/policies/analytics-dev-user-policy.json`.

**Round B: only after the view is switched (step 5).**

4. **Update the runtime policy.** IAM → the runtime policy on `analytics-query-runtime` → Edit → JSON:
   replace it with `infrastructure/policies/analytics-query-runtime-policy.json`. This adds curated read and removes source CSV read.

CLI equivalents (admin profile):

```powershell
$aws = "C:\Users\RamPrasad\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"; $cb = "analytics-poc-curated-791896160716-ap-south-1"
& $aws s3api create-bucket --bucket $cb --region ap-south-1 --create-bucket-configuration LocationConstraint=ap-south-1 --object-ownership BucketOwnerEnforced --profile default
& $aws s3api put-public-access-block --bucket $cb --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true --profile default
& $aws s3api put-bucket-encryption --bucket $cb --server-side-encryption-configuration file://infrastructure/config/results-bucket-encryption.json --profile default
& $aws s3api put-bucket-versioning --bucket $cb --versioning-configuration Status=Enabled --profile default
& $aws s3api put-bucket-policy --bucket $cb --policy file://infrastructure/config/curated-bucket-policy.json --profile default
& $aws athena create-work-group --name analytics_poc_etl_wg --configuration file://infrastructure/config/etl-workgroup.json --region ap-south-1 --profile default
```

## Run commands (dev profile)

```powershell
$py = ".\.venv\Scripts\python.exe"
& $py scripts\verify_athena.py --execute sql\create_curated.sql --workgroup analytics_poc_etl_wg --allow-ctas   # staging view + CTAS
& $py scripts\verify_athena.py --execute sql\reconcile_curated.sql                                              # must show 0 mismatched dates
# switch: the v_account_summary statement from sql\create_typed_views.sql
```

## Cost

The one-time CTAS reads about 29 MB. Reconciliation is a few queries under 100 MB. Storage is well under 10 MB of Parquet.
Total cost is a few cents at most.

## Out of scope

Incremental loads (`INSERT INTO` per new month), converting the other 4 tables, and compaction.
