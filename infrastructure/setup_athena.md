# Athena Setup — analytics_poc (V0.1)

**Status: NOT DEPLOYED.** Every command below changes AWS and needs explicit approval first.

| Resource | Value |
|---|---|
| Account / Region | `791896160716` / `ap-south-1` |
| Source data (read-only) | `s3://opta-s3-demo/nbfc-base-data/<Dataset>/` |
| Query-results bucket | `analytics-poc-athena-results-791896160716-ap-south-1` (SSE-S3, TLS-only, public access blocked, 30-day expiry) |
| Athena workgroup | `analytics_poc_wg`: enforced config, **1 GiB scan cutoff per query**, encrypted results |
| Glue database | `analytics_poc` (created by Athena DDL, **no crawler**) |
| Dev principal | `analytics-dev-user` → profile `analytics-poc` (see `iam_requirements.md`) |

## Prerequisites

```powershell
$aws = "C:\Users\RamPrasad\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"
$py  = ".\.venv\Scripts\python.exe"
$admin = "default"        # admin profile, used ONLY for steps 1–2 (replace root with an admin IAM user when possible)
& $py -m pytest -q        # metadata tests must pass
& $py scripts\generate_sql.py --check
```

## Step 1 — Results bucket (admin) — APPROVAL REQUIRED

```powershell
$rb = "analytics-poc-athena-results-791896160716-ap-south-1"
& $aws s3api create-bucket --bucket $rb --region ap-south-1 `
  --create-bucket-configuration LocationConstraint=ap-south-1 `
  --object-ownership BucketOwnerEnforced --profile $admin
& $aws s3api put-public-access-block --bucket $rb --profile $admin `
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
& $aws s3api put-bucket-encryption --bucket $rb --profile $admin `
  --server-side-encryption-configuration file://infrastructure/config/results-bucket-encryption.json
& $aws s3api put-bucket-lifecycle-configuration --bucket $rb --profile $admin `
  --lifecycle-configuration file://infrastructure/config/results-bucket-lifecycle.json
& $aws s3api put-bucket-policy --bucket $rb --profile $admin `
  --policy file://infrastructure/config/results-bucket-policy.json
```

## Step 2 — Restricted workgroup (admin) — APPROVAL REQUIRED

```powershell
& $aws athena create-work-group --name analytics_poc_wg --region ap-south-1 --profile $admin `
  --description "analytics_poc V0.1 - enforced results location, 1 GiB scan cap" `
  --configuration file://infrastructure/config/workgroup.json `
  --tags Key=project,Value=analytics-poc
```

Verify the setup (read-only, dev profile):

```powershell
& $py scripts\verify_athena.py
```

## Step 3 — Database, raw tables, typed views (dev profile) — APPROVAL REQUIRED

`verify_athena.py --execute` shows every statement and asks you to type `EXECUTE` before it runs anything.
It only allows CREATE DATABASE, CREATE EXTERNAL TABLE, CREATE OR REPLACE VIEW and read-only SELECT statements.

```powershell
& $py scripts\verify_athena.py --execute sql\create_database.sql
& $py scripts\verify_athena.py --execute sql\create_raw_tables.sql
& $py scripts\verify_athena.py --execute sql\create_typed_views.sql
& $py scripts\verify_athena.py          # confirms 5 raw tables + 5 views exist
```

## Step 4 — Validation (dev profile) — APPROVAL REQUIRED

```powershell
& $py scripts\verify_athena.py --execute sql\validate_data.sql
```

There are 27 aggregate-only checks. The whole dataset is about 35 MiB, so a full run scans a few hundred MB at most and costs well under $0.01.
See README → *Validation plan* for how to read the results. If date formats are not ISO,
update `defaults.date_formats` in `semantic/tables.yaml`, run `generate_sql.py`, and re-run step 3 (views only).

## Rollback

```powershell
# Glue metadata only; the source S3 data is never touched
& $aws glue delete-database --name analytics_poc --region ap-south-1 --profile $admin
& $aws athena delete-work-group --work-group analytics_poc_wg --recursive-delete-option --region ap-south-1 --profile $admin
& $aws s3 rb s3://analytics-poc-athena-results-791896160716-ap-south-1 --force --profile $admin
```

## Not used (by design)
Glue crawlers, Bedrock, RDS or any database server, and writes to `opta-s3-demo`.
