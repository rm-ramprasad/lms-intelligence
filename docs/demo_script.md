# Demo script: Portfolio Q&A (about 10 minutes)

**Setup:** `.\.venv\Scripts\streamlit.exe run app\streamlit_app.py` → http://localhost:8501.
In the sidebar, set **Question understanding = Hybrid** and **AI summary = off** to start. Mock NBFC data, as of 31 Mar 2026.

**Message:** business users ask in their own words; every number still comes from governed, tested KPI definitions.

| # | Type this | What to point out |
|---|---|---|
| 1 | `gnpa by state for March 2026` | Badge **"Understood by catalog rules"**: instant and deterministic. Table, chart and **Overall 2.62%**. Open **"How this was calculated"**: definition, formula, snapshot date, query id, MB scanned |
| 2 | `now by loan group` | Follow-up keeps the KPI and period and changes only the breakdown |
| 3 | `how much money is stuck in bad loans` | Badge **"Understood by AI · Amazon Nova Micro"** plus **"Understood as: NPA outstanding · latest"**. The AI only translated the question; the number is the same governed query |
| 4 | `gros npa by stat` | Typos handled by AI; same answer as #1 |
| 5 | `fresh lending last quarter` | AI maps "fresh lending" to Disbursement and "last quarter" to Q3 FY26 (Oct–Dec 2025), using a deterministic calendar it is given |
| 6 | `npa` | Asks rather than guesses: **Gross NPA ratio** or **NPA outstanding**? Click one |
| 7 | `outstanding for account 1234567890` | Refused **before any AI call**. Customer- and account-level data is never sent to the model |
| 8 | `why did NPA increase` | Refused: the assistant reports governed KPIs; it doesn't speculate on causes or forecast |
| 9 | Toggle **AI summary** on, ask `PAR 30 ratio by branch` | A short AI-written summary. Every number in it is checked against the result; if anything doesn't match, the summary is not shown |
| 10 | Sidebar → **Catalog rules**, ask `how big is our lending book right now` | Without AI, the same question gets "I didn't recognise…" with suggestions. Switch back to **Hybrid** to show the difference |
| 11 | Open **KPI catalog** | Every KPI with definition, formula, unit, owner and the words the assistant understands |
| 12 | Repeat question #1 | Served from cache (caption in "How this was calculated"): instant, zero data scanned |

**Numbers to quote** (benchmarks, 19 Oct 2026):
- **Free-form questions** (typos, informal, new synonyms): **93% with Hybrid** vs 36% with catalog rules alone.
- **Existing tested questions:** 98.5–100%.
- **Cost:** about **$0.00015 per AI-interpreted question** (Nova Micro); the 130-question benchmark cost under 2 cents.
- **Speed:** AI interpretation about 0.85 s median; end-to-end answer about 2.7 s median. Repeats are instant (cache).
- **Safety:**
  - Bedrock Guardrail is on: denied topics, PII and prompt-attack filters.
  - The PII pre-filter runs before any AI call.
  - The AI never writes SQL or produces numbers.
