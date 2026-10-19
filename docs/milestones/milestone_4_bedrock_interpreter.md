# Milestone 4 — AI-Assisted Question Understanding with Amazon Bedrock (DRAFT)

We are building a **demo** of a conversational BI application for structured financial data (mock NBFC data).

## Current environment

- Athena over S3. `v_account_summary` reads month-partitioned Parquet. Workgroups have 1 GB scan caps.
- `bi_service`: a governed, deterministic query service. `QuerySpec` → validated, allowlisted SQL → `QueryResult`.
  There is no raw-SQL input path.
- `bi_chat`: a deterministic interpreter over the governed catalog (`glossary.yaml`, `value_aliases.yaml`,
  `dimension_values.yaml`, `language.yaml`). It returns `Interpreted | Clarify | Refuse`.
  The eval set is 64 cases plus 21 golden paraphrases, at 100%.
- `app/`: Streamlit (Ask, KPI catalog, Guided builder), with a 15-minute answer cache. Runs locally.
- The runtime IAM user `analytics-query-runtime` is read-only (Athena workgroup plus curated Parquet).
- Region ap-south-1. Windows plus Claude Code.

Do not recreate infrastructure. Do not change how `bi_service` compiles SQL. Incremental data loads are out of scope.

## Objective

Show that an LLM can make the assistant understand **free-form business language** (paraphrases, typos, informal
phrasing, relative time, follow-ups), while numbers stay **100% governed and deterministic**.

The LLM is only a **translator** from a question to a `QuerySpec` (or to a clarification/refusal), plus an optional
**narrator** of aggregated results. It never writes SQL, never computes numbers, and never sees row-level data.

## Scope change

The Task 1 / Milestone 3 rule "Do not invoke Bedrock" is lifted **for this milestone only**, limited to:
`bedrock:InvokeModel` / `bedrock:Converse` on the approved model or inference profile, called by the runtime user.

## Phase A — Readiness (present before building)

1. Confirm Bedrock model access for the chosen Claude model(s) in ap-south-1. If a model is only available through a
   cross-region inference profile, state which regions may process prompts (a data-residency note for the demo).
2. Model: **lowest cost wins** (decided 2026-10-19).
   - **Amazon Nova Micro** is the default.
   - **Amazon Nova Lite** is the fallback, used only if Micro misses the Phase E accuracy target.
   - No other models are evaluated.
   - Confirm availability in ap-south-1 (directly, or through an APAC cross-region inference profile) and current pricing.
   - Both IAM users currently have an explicit `bedrock:*` deny. Phase A adds:
     - for the dev user only: read-only `bedrock:ListFoundationModels` / `bedrock:ListInferenceProfiles`
     - for the runtime user: invoke on the approved Nova model or profile only
3. Runtime IAM addition: invoke permission on the approved model or inference-profile ARNs only.
   Optional: a Bedrock Guardrail ARN.
4. Estimated cost per question and for the full demo (expected: well under $1 for the eval runs).

## Phase B — LLM interpreter (`bi_chat/llm_interpreter.py`)

- Implements the existing `Interpreter` protocol and returns the same `Interpreted | Clarify | Refuse` types,
  so the UI and `bi_service` don't change.
- **Converse API with tool use.** The model must call exactly one tool:
  - `run_query(spec)`: the JSON schema comes from `QuerySpec`, with enums filled from the catalog
    (approved metric ids, dimension ids, filter values, period kinds)
  - `ask_clarification(question, options[])`
  - `cannot_answer(reason, suggestions[])`
- **The system prompt is generated from the catalog**, never hand-written:
  - KPI names, definitions, synonyms, behavior (snapshot vs period)
  - dimensions and allowed values
  - period rules (fiscal year Apr–Mar named by end year, fiscal quarters, anchor = latest snapshot date)
  - rules: one KPI per question; refuse customer/account-level and why/forecast questions
- **Conversation context:** the previous `QuerySpec` (not result data) is passed for follow-ups.
- **Validation loop:** every proposed spec goes through `bi_service` validation. On rejection, the error codes go back to
  the model once for a correction; after that, a `Clarify` is returned. Invalid specs never reach Athena.
- **Determinism controls:** temperature 0, a fixed model version, a prompt hash in the audit log,
  and a per-question response cache keyed by (question, previous spec, catalog version).

## Phase C — Interpreter modes and safety

- **Modes** (sidebar selector in the demo):
  - `Deterministic`: Milestone 3 as is
  - `AI-assisted`: LLM only
  - `Hybrid` (default for the demo): deterministic first; the LLM only when the deterministic result is
    `Clarify` because of unrecognised words, missing KPI, or ambiguity
- **Pre-filter:** the deterministic refusal rules (identifier patterns, PII words) run **before** any LLM call.
  Questions containing account numbers, PAN, phone or email are never sent to Bedrock.
- **Data minimisation:** the prompt contains only the catalog, the question, the previous spec and the anchor date.
  It never contains result rows or customer data.
- **Optional Bedrock Guardrail:** denied topics (individual customers, credit decisions), prompt-attack filter.
- **Limits:** at most 2 model calls per turn; timeout 10 s; any failure falls back to the deterministic
  interpreter's answer.
- **Transparency:** every answer shows who interpreted it ("Understood by: catalog rules" or "Understood by: AI")
  and the interpreted spec as chips. The user can correct it in the Guided builder.

## Phase D — Optional answer narration (`bi_chat/llm_narrator.py`)

- An optional toggle: the LLM writes a 2–3 sentence business summary from the **aggregated** `QueryResult`
  (at most 50 rows, already formatted) plus the metric definition.
- **Numeric faithfulness check:** every number in the narrative must appear in the formatted result or the total.
  Otherwise fall back to the Milestone 3 template answer.
- Always labelled "AI summary". The table, chart and "How this was calculated" panel are unchanged.

## Phase E — Benchmark and tests

- **Benchmark** (`scripts/benchmark_interpreters.py`): run the 85 existing eval items **plus a new "free-form" set**
  (≥ 40 questions with typos, informal phrasing, unseen synonyms, relative dates, multi-turn follow-ups) through
  Deterministic, AI-assisted (Nova Micro; Nova Lite only if Micro misses the target) and Hybrid. Report:
  - spec exact-match rate, clarification/refusal correctness
  - latency p50/p95, tokens and cost per question
  - questions newly answered by the LLM vs deterministic
- **Unit tests with a mocked Bedrock client:**
  - tool-call parsing; schema rejection; the validation retry loop
  - pre-filter blocks PII before any call
  - timeout/failure fallback; narration faithfulness check
  - the prompt contains no row data (asserted)
- The existing 259 tests stay green. The live benchmark runs only with approval.

## Acceptance criteria

1. Hybrid mode passes 100% of the existing deterministic eval set (no regression).
2. On the free-form set, Hybrid answers at least 85% correctly (exact spec, or correct clarification/refusal),
   versus a measured deterministic baseline.
3. 0 invalid specs reach Athena; 0 questions containing identifiers are sent to Bedrock (tested).
4. Every displayed number comes from `bi_service`. Narration passes the faithfulness check or falls back.
5. Median end-to-end latency ≤ 4 s for AI-interpreted questions (excluding cache hits). Cost per question is reported.
6. The demo shows: a free-form question, a typo, a follow-up, a clarification, a refusal, and the
   "Understood by" badge and calculation panel.
7. Tests pass locally.

## Demo script (deliverable)

A one-page walkthrough (`docs/demo_script.md`) with 8–10 prepared questions that show:
- deterministic vs AI understanding
- governed numbers
- refusals
- follow-ups
- the KPI catalog
- the "how this was calculated" transparency

## Out of scope

Incremental loads, SQL generation by the LLM, new KPIs without approval, multi-KPI questions (still one KPI per question),
joins across views, authentication and shared hosting, production hardening.

## Working method

First review the repository and confirm Bedrock model availability (read-only).

Present:
- Phase A decisions (Nova Micro availability, region / inference profile, IAM change, cost estimate)
- Tool schema and a sample of the catalog-generated system prompt
- Hybrid routing rules and the safety pre-filter
- Free-form test set outline
- Implementation plan

Do not implement until I approve. After approval, implement, run the tests and the (approved) live benchmark, and
stop before any shared deployment.
