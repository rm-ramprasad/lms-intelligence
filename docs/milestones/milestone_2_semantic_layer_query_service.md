# Milestone 2 — Build Semantic Layer and Deterministic Query Service

We are building V0.1 of a conversational BI application for structured financial data.

## Current environment

- CSV source files are stored in Amazon S3.
- Athena external tables and typed SQL views already exist.
- AWS access is configured locally.
- Development is through Claude Code on Windows.
- Future frontend: Streamlit.
- Future natural-language interpretation: Amazon Bedrock.

Do not recreate existing AWS infrastructure.

## Objective

Build a reusable Python query service that accepts structured business queries, compiles them into governed Athena SQL, executes the SQL and returns structured results.

The service must be independent of Streamlit, Bedrock and any agent framework.

## Phase A — Discover existing Athena views

1. Verify the configured AWS identity and region.
2. Ask me for the Athena database and workgroup.
3. Discover existing views and their schemas using Boto3 or AWS CLI.
4. Retrieve view definitions where permissions allow.
5. Identify available dates, measures, dimensions and potential joins.
6. Do not retrieve or expose customer-level records to an LLM.
7. Generate a metadata inventory.

## Phase B — Semantic layer

Create:

- semantic/tables.yaml
- semantic/metrics.yaml
- semantic/dimensions.yaml
- semantic/relationships.yaml
- semantic/examples.yaml

For each metric, define:

- Metric ID and business name
- Source view
- Approved aggregation
- Snapshot or period behavior
- Required filters
- Allowed grouping dimensions
- Data type and formatting
- Business definition
- Validation SQL

Do not invent financial KPI definitions or relationships. Mark uncertain definitions as pending approval.

## Phase C — Query service

Implement:

QuerySpec:
- metric
- dimensions
- filters
- date period
- sort
- limit

QueryResult:
- status
- columns
- rows
- row_count
- metric metadata
- query_id
- execution time
- error details when applicable

Build these modules:

- semantic_loader.py
- query_models.py
- query_validator.py
- sql_compiler.py
- athena_executor.py
- query_service.py

The SQL compiler must generate SQL only from approved semantic definitions.

Do not accept arbitrary SQL from users or an LLM.

Use parameter validation, identifier allowlists and safe literal construction.

## Phase D — Security

- Only approved Athena views may be queried.
- No DDL, DML or multi-statement SQL.
- Enforce dimension and filter permissions.
- Enforce result row limits.
- Use an approved Athena workgroup.
- Enforce Athena scan limits and query timeouts.
- Restrict access using AWS IAM.
- Keep customer data out of application logs.
- Ensure runtime access is read-only for source data.

## Phase E — Testing

Create pytest tests for:

- Valid metric queries
- Invalid metric names
- Unauthorized dimensions
- Invalid filters
- Snapshot-date handling
- Fully additive vs semi-additive metrics
- Ratio KPI calculations
- Zero denominators
- SQL injection attempts
- Athena execution errors
- Empty results
- Query timeout and cancellation

Mock AWS interactions for unit tests.

## Acceptance criteria

1. The service answers at least five approved business questions.
2. Every generated SQL query uses governed Athena views.
3. Financial KPI results match independently verified Athena SQL.
4. Invalid queries are rejected before execution.
5. The query service runs without Streamlit or Bedrock.
6. Tests pass locally.

## Working method

First inspect the existing repository and Athena metadata.

Present:
- Proposed semantic model
- Proposed file structure
- KPI ambiguities
- Implementation plan

Do not implement until I approve the plan.

After approval, implement the query service and run tests.

Stop before integrating Bedrock or Streamlit.
