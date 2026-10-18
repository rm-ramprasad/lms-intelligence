# IAM Requirements — analytics_poc (V0.1)

## Principals

| Principal | Purpose | Notes |
|---|---|---|
| `analytics-dev-user` | Day-to-day dev: S3 discovery, Athena DDL/queries | Local profile `analytics-poc`. Policy: `policies/analytics-dev-user-policy.json` |
| Account root | **Not used** | Root access keys must be deleted after the dev profile works |

One-time admin actions (creating the results bucket and Athena workgroup) are done by an
admin principal, not by `analytics-dev-user`.

## What the dev policy allows

- **Source data (read only):** list and get on `s3://opta-s3-demo/nbfc-base-data/*`. Writes, deletes and bucket-policy changes are explicitly denied.
- **Athena:** start, stop and get queries **only in workgroup `analytics_poc_wg`**. That workgroup enforces its own results location, encryption and scan cap.
- **Athena results:** read and write on `s3://analytics-poc-athena-results-791896160716-ap-south-1/`.
- **Glue Data Catalog:** database and table operations limited to `analytics_poc`. This is needed because Athena DDL writes to Glue.
- **Explicit denies:** Glue crawlers, Bedrock and RDS.

## Setup commands (run as admin; requires approval)

```powershell
$aws = "C:\Users\RamPrasad\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"

# 1. Attach the inline least-privilege policy
& $aws iam put-user-policy --user-name analytics-dev-user `
  --policy-name analytics-poc-dev `
  --policy-document file://infrastructure/policies/analytics-dev-user-policy.json

# 2. Create an access key (the secret is shown once; do not paste it into chat or code)
& $aws iam create-access-key --user-name analytics-dev-user

# 3. Configure the local profile interactively (credentials are stored in ~/.aws/credentials, never in the repo)
& $aws configure --profile analytics-poc     # region: ap-south-1, output: json

# 4. Verify
& $aws sts get-caller-identity --profile analytics-poc
```

Prefer IAM Identity Center (`aws configure sso`) over long-lived keys if it's available.

## After verification
- Delete the root access keys: Console → account menu → Security credentials.
- Enable MFA on root.
