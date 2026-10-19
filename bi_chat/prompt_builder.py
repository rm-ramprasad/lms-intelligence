"""System prompt and tool schemas, generated from the governed catalog (never hand-written).

Enums in the tool schema are the approved ids/values, so the model cannot name anything outside
the catalog. Only catalog text, the question, the previous spec and the anchor date are sent:
never result rows or customer data.
"""
import hashlib
import json
from datetime import date

from bi_service.semantic_loader import SemanticModel

from .catalog import Catalog

REFUSE_REASONS = ["customer_or_account_level", "explain_or_forecast", "not_an_approved_kpi"]


def _approved(model: SemanticModel) -> list[str]:
    return [m for m, x in model.metrics.items() if x.approved]


def build_tools(model: SemanticModel, catalog: Catalog) -> list[dict]:
    metrics = _approved(model)
    groupable = [d for d, x in model.dimensions.items() if x.groupable]
    filterable = [d for d, x in model.dimensions.items() if x.filterable]
    return [
        {"name": "run_query",
         "description": "Answer the question with exactly one approved KPI, optional breakdowns, filters and period.",
         "inputSchema": {"json": {
             "type": "object", "additionalProperties": False, "required": ["metric"],
             "properties": {
                 "metric": {"type": "string", "enum": metrics},
                 "dimensions": {"type": "array", "maxItems": 2, "items": {"type": "string", "enum": groupable}},
                 "filters": {"type": "array", "maxItems": 5, "items": {
                     "type": "object", "required": ["dimension", "op", "value"],
                     "properties": {
                         "dimension": {"type": "string", "enum": filterable},
                         "op": {"type": "string", "enum": ["eq", "ne", "in", "not_in"]},
                         "value": {"description": "A listed value, a list of listed values, or true/false for flags"}}}},
                 "period": {"type": "object", "properties": {
                     "kind": {"type": "string", "enum": ["latest", "month", "fiscal_year", "calendar_year", "range"]},
                     "month": {"type": "string", "description": "YYYY-MM when kind=month"},
                     "year": {"type": "integer", "description": "fiscal or calendar year when kind is *_year"},
                     "start": {"type": "string", "description": "YYYY-MM-DD when kind=range"},
                     "end": {"type": "string", "description": "YYYY-MM-DD when kind=range"}}},
                 "rank": {"type": "string", "enum": ["highest_first", "lowest_first"]},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 100}}}}},
        {"name": "ask_clarification",
         "description": "Ask the user to choose when the question is ambiguous or names more than one KPI.",
         "inputSchema": {"json": {
             "type": "object", "required": ["question", "options"],
             "properties": {"question": {"type": "string"},
                            "options": {"type": "array", "minItems": 2, "maxItems": 4,
                                        "items": {"type": "string", "enum": metrics}}}}}},
        {"name": "cannot_answer",
         "description": "Use when the question is about individual customers/accounts, asks why/forecasts, or needs a KPI that is not listed.",
         "inputSchema": {"json": {
             "type": "object", "required": ["reason"],
             "properties": {"reason": {"type": "string", "enum": REFUSE_REASONS}}}}},
    ]


def _calendar(anchor: date) -> list[str]:
    """Pre-computed period table so the model never does date arithmetic (small models get it wrong)."""
    from .period_parser import _fq_of, _fq_range, _shift_quarter, fiscal_year_of

    def rng(s: date, e: date) -> str:
        return f'{{"kind": "range", "start": "{s.isoformat()}", "end": "{e.isoformat()}"}}'

    def month_back(n: int) -> date:
        y, m = anchor.year, anchor.month - n
        while m < 1:
            y, m = y - 1, m + 12
        return date(y, m, 1)

    fy = fiscal_year_of(anchor)
    q, qfy = _fq_of(anchor)
    lq, lqfy = _shift_quarter(q, qfy, -1)
    ya = month_back(12)
    rows = [
        f'- latest / now / this month / last month -> {{"kind": "latest"}}  (latest month-end {anchor.isoformat()})',
        f'- this fiscal year / YTD / FYTD / since April -> {rng(date(fy - 1, 4, 1), anchor)}',
        f'- FY {fy} -> {{"kind": "fiscal_year", "year": {fy}}}; last year / last fiscal year / FY {fy - 1} -> {{"kind": "fiscal_year", "year": {fy - 1}}}',
        f'- first half of this fiscal (H1 FY{fy % 100}) -> {rng(date(fy - 1, 4, 1), date(fy - 1, 9, 30))}; '
        f'second half (H2 FY{fy % 100}) -> {rng(date(fy - 1, 10, 1), min(date(fy, 3, 31), anchor))}',
        f'- this quarter (Q{q} FY{qfy % 100}) -> {rng(*_fq_range(q, qfy))}; last quarter / end of last quarter '
        f'(Q{lq} FY{lqfy % 100}) -> {rng(*_fq_range(lq, lqfy))}',
        f'- last 3 months -> {rng(month_back(2), anchor)}; last 6 months -> {rng(month_back(5), anchor)}; '
        f'last 12 months -> {rng(month_back(11), anchor)}; last two quarters -> {rng(_fq_range(*_shift_quarter(q, qfy, -1))[0], anchor)}',
        f'- a year ago -> {{"kind": "month", "month": "{ya:%Y-%m}"}}',
    ]
    for fyx in (fy, fy - 1):
        rows.append(f"- FY{fyx % 100} quarters: " + "; ".join(
            f"Q{i} FY{fyx % 100} -> {rng(*_fq_range(i, fyx))}" for i in range(1, 5)))
    rows.append(f"- 'since <Month>' -> range from the 1st of the most recent such month on or before {anchor.isoformat()} "
                f"to {anchor.isoformat()}. Never use dates after {anchor.isoformat()}.")
    return rows


CONCEPT_HINTS = [
    "- bad loans / bad debt / non-performing / defaulted / stressed book -> NPA KPIs "
    "(gross_npa_ratio for share/ratio/%, npa_outstanding for amount)",
    "- overdue / delinquent / past due / at risk / slipping -> PAR 30+ KPIs "
    "(par_30_plus_ratio for share/ratio/%, par_30_plus_amount for amount)",
    "- lending / lent / loans given / fresh lending / new loans booked -> disbursement_amount",
    "- book / portfolio / AUM / POS / exposure / loan book size -> total_outstanding",
    "- collected / collections / recovered -> repayment_amount; collection efficiency / CE -> collection_efficiency",
    "- billed / instalments due / EMIs raised -> demand_amount",
    "- 'monthly', 'month-wise', 'each month', 'trend' -> add the month breakdown; 'quarterly' -> quarter; "
    "'yearly' -> fiscal_year",
]


def build_system_prompt(model: SemanticModel, catalog: Catalog, anchor: date) -> str:
    fy = anchor.year + 1 if anchor.month >= 4 else anchor.year
    lines = [
        "You translate business questions about a loan portfolio into exactly ONE tool call.",
        "You never write SQL and never compute or state numbers.",
        f"The latest month-end snapshot is {anchor.isoformat()} (current fiscal year FY {fy}).",
        "Fiscal year runs April-March and is named by its end year (FY 2026 = 2025-04-01..2026-03-31).",
        "Fiscal quarters: Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar. 'YTD' and 'last year' are fiscal.",
        "",
        "Period table (copy these exactly; do not calculate dates yourself):",
        *_calendar(anchor),
        "",
        "Business concepts:",
        *CONCEPT_HINTS,
        "",
        "KPIs (id | name | meaning | also called):",
    ]
    for mid in _approved(model):
        k, m = catalog.kpis[mid], model.metrics[mid]
        kind = "month-end balance" if m.behavior == "snapshot" else "monthly flow; needs a period"
        lines.append(f"- {mid} | {k.name} | {k.short} ({kind}) | {', '.join(k.synonyms)}")
    lines += ["", "Breakdowns (id | name | also called | allowed filter values):"]
    for did, d in model.dimensions.items():
        syn = ", ".join(catalog.dimension_synonyms.get(did, ())[:5])
        if d.kind == "time":
            vals = "time grain (not filterable; use period)"
        elif d.type == "boolean":
            vals = "true / false"
        else:
            vals = ", ".join(map(str, catalog.values.get(did, [])))
        lines.append(f"- {did} | {d.label} | {syn} | {vals}")
    lines += [
        "",
        "Rules:",
        "1. Exactly one KPI per question. If the user names two KPIs, call ask_clarification with both.",
        "2. If no KPI is named or clearly implied (e.g. 'how are we doing', 'show me the risk', 'npa numbers'), "
        "call ask_clarification with the 2-4 most likely KPI ids. Never guess.",
        "3. Use only the ids and filter values listed above; map synonyms, abbreviations and typos to them.",
        "4. Periods: use the period table above. Flow KPIs with no period and no time breakdown -> kind=latest.",
        "5. 'Top/highest N' -> rank=highest_first and limit=N; 'bottom/lowest N' -> rank=lowest_first. "
        "'Which X has the most/highest' -> rank=highest_first, limit=1.",
        "6. cannot_answer ONLY for: individual customers/borrowers/accounts; 'why'/causes, forecasts or advice; "
        "topics unrelated to these KPIs. Otherwise prefer ask_clarification.",
        "7. If a previous query is given, the question may be a follow-up: change only what the user mentions "
        "and keep everything else (metric, breakdowns, filters, period).",
    ]
    return "\n".join(lines)


def build_user_message(question: str, previous_spec: dict | None, chosen_metric: str | None = None) -> str:
    parts = [f"Question: {question}"]
    if previous_spec:
        keep = {k: previous_spec[k] for k in ("metric", "dimensions", "filters", "period") if k in previous_spec}
        parts.append("Previous query: " + json.dumps(keep, default=str))
    if chosen_metric:
        parts.append(f"The user has chosen the KPI: {chosen_metric}")
    return "\n".join(parts)


def prompt_hash(system: str, tools: list[dict]) -> str:
    return hashlib.sha256((system + json.dumps(tools, sort_keys=True)).encode()).hexdigest()[:12]
