# Milestone 4 — Plan (for approval)

Status: **PROPOSED, not implemented.** Brief: [milestone_4_bedrock_interpreter.md](milestone_4_bedrock_interpreter.md).

## 1. Phase A findings (read-only, 2026-10-19)

| Item | Finding |
|---|---|
| Nova Micro in ap-south-1 | `amazon.nova-micro-v1:0`, ACTIVE, **inference-profile only** (no on-demand direct invoke) |
| Profile to use | `apac.amazon.nova-micro-v1:0` (ACTIVE) |
| Where prompts may be processed | ap-south-1 (Mumbai), ap-southeast-1 (Singapore), ap-southeast-2 (Sydney), ap-northeast-1 (Tokyo), ap-northeast-2 (Seoul), ap-northeast-3 (Osaka) |
| Fallback | `apac.amazon.nova-lite-v1:0` (ACTIVE), used only if Micro misses the accuracy target |
| Runtime IAM | `bedrock:InvokeModel` on the APAC Micro profile plus those 6 regional model ARNs only (file updated; **re-apply needed**) |
| Dev IAM | Bedrock list/get only. Invoke stays denied |

**Data residency note for the demo:** prompts contain only the catalog, the question text and the previous query spec.
They may be processed in any of the 6 APAC regions. No customer data or result rows are ever sent.

**Cost estimate (Nova Micro, list pricing; ap-south-1 to be confirmed on the bill):**

| | Tokens | Cost |
|---|---|---|
| System prompt (catalog) + tool schema | ~3,500 in | ~$0.00012 |
| Question + previous spec | ~100 in | — |
| Tool-call reply | ~150 out | ~$0.00002 |
| **Per AI-interpreted question** | | **~$0.00015** |
| Full benchmark (~125 questions × 2 modes, plus retries) | | **< $0.10** |

In Hybrid mode, Bedrock is called only when the catalog rules can't interpret the question.

## 2. Tool schema (what Nova must return)

The model is forced to call exactly one tool (`toolChoice: any`):

```json
{"name": "run_query", "description": "Answer with one approved KPI",
 "inputSchema": {"json": {
   "type": "object", "additionalProperties": false, "required": ["metric"],
   "properties": {
     "metric":     {"enum": ["total_outstanding", "gross_npa_ratio", "...approved ids only"]},
     "dimensions": {"type": "array", "maxItems": 2, "items": {"enum": ["state_name", "month", "...allowed ids"]}},
     "filters":    {"type": "array", "items": {"type": "object", "required": ["dimension", "op", "value"],
                    "properties": {"dimension": {"enum": ["...filterable ids"]}, "op": {"enum": ["eq", "ne", "in", "not_in"]},
                                   "value": {}}}},
     "period":     {"type": "object", "properties": {"kind": {"enum": ["latest", "month", "fiscal_year", "calendar_year", "range"]},
                    "month": {"type": "string"}, "year": {"type": "integer"}, "start": {"type": "string"}, "end": {"type": "string"}}},
     "sort_desc":  {"type": "boolean"}, "limit": {"type": "integer", "maximum": 100}}}}}
{"name": "ask_clarification", "inputSchema": {"json": {"required": ["question", "options"], "...": "options = approved KPI ids or short phrases"}}}
{"name": "cannot_answer",     "inputSchema": {"json": {"required": ["reason"], "...": "reason enum: customer_level | explain_or_forecast | not_a_kpi"}}}
```

Enums are **generated from the catalog**, so the model cannot name an unapproved KPI, dimension or value.
Filter values are re-checked against `dimension_values.yaml` and then by the `bi_service` validator.

## 3. System prompt (generated from the catalog; excerpt)

```
You translate business questions about a loan portfolio into ONE tool call. You never write SQL and never compute numbers.
Today's data is as of the latest month-end snapshot: 2026-03-31. Fiscal year = Apr–Mar, named by end year (FY 2026 = Apr 2025–Mar 2026).
Fiscal quarters: Q1 Apr–Jun … Q4 Jan–Mar. "Last year" / "YTD" are fiscal.

KPIs (id — name — meaning — also called):
- total_outstanding — Total outstanding — month-end balance; never add across months — outstanding, loan book, AUM, POS, portfolio…
- gross_npa_ratio — Gross NPA ratio — NPA outstanding ÷ total outstanding — gnpa, npa %, npa ratio…
- disbursement_amount — Disbursement — monthly flow; needs a period — disbursal, disbursed…
  …(all 12 approved KPIs)
Breakdowns: state_name (state, region) values: Delhi, Gujarat, …; branch_code: DEMO-EAST, …; month / quarter / fiscal_year (time) …
Rules:
1. Exactly one KPI. If two are asked, call ask_clarification.
2. If the KPI is unclear (e.g. "npa" could be ratio or amount), call ask_clarification with the candidate KPI ids.
3. Customer/account-level, "why", forecasts → cannot_answer.
4. Use only ids and values listed above. Map synonyms and typos to them.
5. Follow-up questions modify the PREVIOUS SPEC below; keep everything not mentioned.
Previous spec: {"metric": "gross_npa_ratio", "dimensions": ["state_name"], "filters": []}
```

The prompt is versioned by hash and logged with each AI turn. The prompt and catalog together are cached with Bedrock
prompt caching if Nova supports it; otherwise they're sent each time (~3.5k tokens, which is cheap).

## 4. Routing: Hybrid mode (demo default)

```
question
  │ deterministic refusal pre-filter (identifiers, PII words, why/forecast)  → Refuse   [never sent to Bedrock]
  │ deterministic interpreter
  ├─ Interpreted                                    → execute (badge: "catalog rules")
  ├─ Refuse (pending KPI)                           → Refuse
  ├─ Clarify: ambiguous KPI / >1 KPI / flow without period → show the deterministic clarification (no AI)
  └─ Clarify: unrecognised words or no KPI found    → Nova Micro
        ├─ run_query → bi_service.validate ── ok → execute (badge: "AI")
        │                                  └─ invalid → 1 retry with error codes → else Clarify
        ├─ ask_clarification → buttons
        └─ cannot_answer → Refuse
  Any Bedrock error/timeout (10 s) → fall back to the deterministic clarification.
```

AI-interpreted answers always show the interpreted spec, so the user can see and correct what was understood.

## 5. Free-form test set outline (`tests/eval/freeform.yaml`, ~45 cases)

| Group | # | Examples |
|---|---|---|
| Typos / misspellings | 8 | "gros npa by stat", "disbursment fy26", "outstnding in karnatka" |
| Informal phrasing | 8 | "how much money is stuck in bad loans", "how big is our book in the south" |
| Unseen synonyms | 6 | "delinquency rate by branch" (should clarify), "loan portfolio size" |
| Relative time | 6 | "since April", "first half of this fiscal", "end of last quarter" |
| Multi-turn | 6 | "and for Karnataka?", "break that down monthly", "compare with last year" |
| Must clarify | 5 | "how are we doing", "npa numbers" |
| Must refuse | 6 | "which borrowers defaulted", "predict NPA", "account AB12345678" (pre-filter, no AI call) |

Each case lists the expected spec or outcome. The benchmark reports deterministic vs AI vs hybrid.

## 6. File structure

```
bi_chat/
  llm_client.py        Bedrock Converse wrapper (timeout, retries, token/cost logging); injectable for tests
  prompt_builder.py    system prompt + tool schemas from catalog; prompt hash
  llm_interpreter.py   Interpreter protocol implementation (tool call → Interpreted | Clarify | Refuse)
  hybrid.py            routing rules above
  llm_narrator.py      optional AI summary + numeric faithfulness check
app/                   sidebar: interpreter mode (Deterministic / AI / Hybrid), AI summary toggle; "Understood by" badge
scripts/benchmark_interpreters.py
tests/  test_prompt_builder.py test_llm_interpreter.py test_hybrid.py test_narrator.py  eval/freeform.yaml
docs/demo_script.md
```

The config adds `bedrock.profile_id`, `bedrock.timeout_seconds`, `bedrock.max_tokens` and `bedrock.enabled` to `tables.yaml` (aws block).

## 7. Implementation order

1. Prompt builder and tool schemas, with tests (offline).
2. LLM client and interpreter with a **mocked** Bedrock client: parsing, validation retry, fallback, PII never sent.
3. Hybrid router plus app integration (mode selector, badge), and the AppTest smoke test.
4. Narrator plus faithfulness check (mocked).
5. **Live:** one smoke call, then the benchmark on Nova Micro (approval needed; < $0.10). Nova Lite only if Micro < 85%.
6. Demo script, README and CLAUDE.md updates.

## 7a. Status (2026-10-19)

**Steps 1–4 are implemented and tested offline** with Bedrock mocked:
- `prompt_builder`, `llm_client`, `llm_interpreter`, `hybrid`, `llm_narrator`
- app modes, badge and summary toggle
- 318 tests pass

**Deterministic baseline (no AI):**

| Set | Score |
|---|---|
| Free-form | 16/44 (36.4%) |
| Existing eval set | 67/67 |
| Golden paraphrases | 21/21 |

**Admin steps before the live run (step 5):**

1. **Create the guardrail** (admin profile):
   ```powershell
   $aws = "C:\Users\RamPrasad\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"
   & $aws bedrock create-guardrail --cli-input-json file://infrastructure/config/guardrail.json --region ap-south-1 --profile default
   & $aws bedrock create-guardrail-version --guardrail-identifier <guardrailId from above> --region ap-south-1 --profile default
   ```
   Send the `guardrailId`; it goes into `tables.yaml` → `bedrock.guardrail_id`, version "1".
2. **Re-apply `infrastructure/policies/analytics-query-runtime-policy.json`** to the runtime user. It now has
   Nova Micro invoke on the 6 APAC ARNs plus `bedrock:ApplyGuardrail` on this account's guardrails in ap-south-1.

## 7b. Live results (2026-10-19, Nova Micro via APAC profile, guardrail ch7aqg7t3js4 DRAFT)

| Mode | Free-form (44) | Eval (67) | Golden (21) | AI calls | Cost | AI p50 / p95 |
|---|---|---|---|---|---|---|
| Catalog rules | 36.4% | 100% | 100% | 0 | $0 | — |
| Hybrid, run 1 | 68.2% | 97.0% | 100% | 37 | $0.0038 | 849 / 1857 ms |
| **Hybrid, run 2** | **93.2%** | **98.5%** | **100%** | 31 | $0.0045 | 849 / 951 ms |
| AI only, run 2 | 88.6% | 62.7% | 85.7% | 120 | $0.0177 | 858 / 1028 ms |

**Changes between run 1 and run 2:**
- Hybrid calls AI only when the question has words the catalog doesn't recognise.
- A pre-computed period table in the prompt.
- Concept hints in the prompt.
- `cannot_answer` restricted to the three refusal categories.
- Three free-form cases gained a documented `accept` alternative (marked in `freeform.yaml`).

**End-to-end** (AI interpretation plus Athena, 5 questions): median **2.7 s**.

**Acceptance:**

| # | Criterion | Result |
|---|---|---|
| 1 | No regression on the existing eval set | Met with one exception: 66/67. "outstanding by state blah" is now answered (the AI ignores the noise word) instead of clarified |
| 2 | ≥ 85% on free-form | Met: 93.2% |
| 3 | No invalid specs to Athena; no identifiers to Bedrock | Met: enforced by the validator and pre-filter, with tests |
| 4 | Numbers only from bi_service; summary faithfulness check | Met |
| 5 | Median end-to-end ≤ 4 s | Met: 2.7 s |
| 6 | Demo script | Met: `docs/demo_script.md` |
| 7 | Tests pass | Met: 323 offline |

**Guardrail:** version 1 published and in use (`tables.yaml`). Live check: a normal question passed; a prompt attack and an
investment-advice question were both blocked (`reason=guardrail`).

## 8. Decisions needed

| # | Decision | Proposed default |
|---|---|---|
| E1 | Accept APAC cross-region processing (6 regions) for the demo | **APPROVED 2026-10-19.** Nova Micro through `apac.amazon.nova-micro-v1:0`; demo/mock data only. A Mumbai-only option was evaluated: Nova isn't on-demand in ap-south-1 |
| E2 | Include the optional AI summary (narration) | **APPROVED.** Off by default, with a toggle and faithfulness check |
| E3 | Default interpreter mode in the demo | **APPROVED.** Hybrid |
| E4 | Bedrock Guardrail | **APPROVED: include.** On by default for AI calls (interpreter and summary): denied topics, PII filter, prompt-attack filter |
| E5 | Re-apply the runtime policy (narrowed to the 6 APAC model ARNs) | Yes, before step 5 |
