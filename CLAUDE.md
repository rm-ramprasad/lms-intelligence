# CLAUDE.md — analytics_poc (conversational BI, V0.1: data foundation)

## Environment
- Windows + PowerShell. AWS CLI: `C:\Users\RamPrasad\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe`
- Python: use the venv `.\.venv\Scripts\python.exe` (base install `C:\Users\RamPrasad\AppData\Local\Python\bin\python.exe`; the `python` on PATH is a Store stub).
- AWS profile `analytics-poc` (IAM user `analytics-dev-user`), region `ap-south-1`. Never use the `default` (root) profile except for approved admin steps in `infrastructure/setup_athena.md`.

## Source of truth
- `semantic/tables.yaml` defines every table and column. `sql/create_raw_tables.sql`, `sql/create_typed_views.sql` and `sql/validate_data.sql` are **generated**. Edit the YAML, then run `scripts/generate_sql.py`. Never hand-edit the generated SQL.
- Run `.\.venv\Scripts\python.exe -m pytest -q` after any change.

## Query service (`bi_service/`)
- Queries are compiled only from `semantic/metrics.yaml` and `semantic/dimensions.yaml`. Never add a raw-SQL input path.
- To add a metric, add it to `metrics.yaml` with `status: pending_approval`. Only the user sets `approved`.
- Snapshot metrics are semi-additive (they can be summed within one month-end, never across months). Period metrics are additive.
- V0.1 is single-view (`v_account_summary`). Relationships stay `enabled: false` until approved.
- `pytest -m live` hits Athena. Run it only with approval.

## Chat interface (`bi_chat/`, `app/`)
- **The deterministic interpreter** (`interpreter.py`, `matcher.py`) uses no LLM. It works from `semantic/glossary.yaml`,
  `value_aliases.yaml`, `dimension_values.yaml` and `language.yaml`. The eval set must stay at 100%. LLM use is confined to the
  Milestone 4 AI modules (see below), and a test enforces this.
- **Layering:** `app → bi_chat → bi_service`. Never import upward. `bi_chat` must not import streamlit.
- **New vocabulary** goes into the YAML catalog (new KPI synonyms need owner approval). Then run the eval set
  (`tests/eval/questions.yaml`); it must stay at 100%.
- **Never auto-run** a "Did you mean" suggestion. The user must click it.
- **Refreshing values:** `scripts/refresh_values.py` runs one aggregate Athena query, so it needs approval.

## AI interpretation (Milestone 4: Bedrock Nova Micro, APAC cross-region; demo data only)
- Bedrock may be called only from `bi_chat/llm_client.py`, through `llm_interpreter.py` / `llm_narrator.py`.
  `bi_service` never calls an LLM. The model only returns tool calls (a QuerySpec, a clarification limited to approved KPI
  ids, or a refusal). It never writes SQL and never computes numbers.
- **PII pre-filter:** the deterministic refusal check runs before any Bedrock call. Questions with identifiers or PII are
  never sent. Prompts contain catalog text, the question, the previous spec and the anchor date only, never rows.
- **AI summary:** every number in it must appear in the aggregated input. Otherwise the template answer is shown.
- **Guardrail:** `bedrock.guardrail_id` in tables.yaml. It's on for all AI calls when set.
- **Live calls:** `scripts/benchmark_interpreters.py --modes ai|hybrid` and the app's AI mode call Bedrock. Run the
  benchmark only with approval.

## Hard rules
- **Data access:** never read, download or print customer records or row-level data. The only approved data access is `scripts/profile_s3.py`: it runs in memory, skips PII columns, and outputs aggregates only. Validation output must stay aggregate-only.
- **Column quality:** columns tagged `quality: placeholder|all_zero|constant` must not be used in metrics or as dimensions.
- **PII:** columns marked `quasi`, `sensitive` or `direct` never appear in views, validation queries, metrics, or anything sent to an LLM. Exception: `dim_customers` and `dim_adm` have `expose_pii_in_view: true` (approved 2026-10-18 because the data is mock). Its classifications are kept, and the flag must be turned off before any real customer data is loaded. Identifiers (`account_number`, `customer_id`) may be counted, never displayed.
- **Approval:** never execute SQL or change AWS infrastructure without explicit approval in the current conversation. Run SQL only through `scripts/verify_athena.py --execute`, in workgroup `analytics_poc_wg`.
- **Source bucket:** never write to, delete from, or change the policy of `s3://opta-s3-demo`.
- **Out of scope:** no Glue crawlers, Bedrock, RDS or other databases.
- **Credentials:** never put credentials in code or files. Use profiles only.
- **Relationships:** do not assume relationships or joins between tables until they are approved.
- **Inferred types:** column types were inferred from names alone. Treat `review:` notes as open questions, not facts.
