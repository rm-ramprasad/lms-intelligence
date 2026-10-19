"""Optional AI summary of an AGGREGATED result. The model sees only formatted aggregate values
(at most 50 rows), the KPI definition and the period. A numeric faithfulness check rejects any
summary containing a number that is not in that input; the caller then shows the template answer."""
import re

from bi_service.formatting import date_label, format_value
from bi_service.query_models import QueryResult
from bi_service.semantic_loader import SemanticModel

from .answer_renderer import dimension_label, when_text
from .catalog import Catalog
from .llm_client import BedrockClient, LlmError

MAX_ROWS = 50
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

SYSTEM = (
    "You write a 2-3 sentence business summary of a KPI result for a lending company manager. "
    "Use ONLY the numbers given, copied exactly as written (same units and decimals). "
    "Do not calculate new numbers, do not speculate about causes, do not give advice, do not mention customers."
)


def _norm(n: str) -> str:
    return n.replace(",", "")


def numbers_in(text: str) -> set[str]:
    return {_norm(m) for m in NUMBER.findall(text)}


def build_input(result: QueryResult, spec: dict, model: SemanticModel, catalog: Catalog) -> str:
    metric = model.metrics[spec["metric"]]
    kpi = catalog.kpis[metric.id]
    vfmt = metric.format
    dims = [c.name for c in result.columns if c.role == "dimension"]
    lines = [f"KPI: {kpi.name}. Definition: {kpi.long}",
             f"Period: {when_text(result, model, metric.id)}. Snapshot date: {date_label(result.snapshot_date)}."]
    if dims:
        lines.append("Breakdown by " + ", ".join(model.dimensions[d].label for d in dims) + ":")
    for r in result.rows[:MAX_ROWS]:
        label = " / ".join(dimension_label(model, d, r[i]) for i, d in enumerate(dims)) or "Value"
        lines.append(f"- {label}: {format_value(r[-1], vfmt)}")
    if result.total is not None:
        lines.append(f"Overall: {format_value(result.total[-1], vfmt)}")
    return "\n".join(lines)


def narrate(client: BedrockClient, result: QueryResult, spec: dict, model: SemanticModel,
            catalog: Catalog) -> tuple[str | None, dict]:
    """Returns (summary or None, meta). None means: show the template answer instead."""
    if result.status != "success" or not result.rows:
        return None, {"reason": "no_data"}
    content = build_input(result, spec, model, catalog)
    try:
        r = client.converse(SYSTEM, [{"role": "user", "content": [{"text": content}]}])
    except LlmError as e:
        return None, {"reason": f"error:{e}"}
    meta = {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "latency_ms": r.latency_ms,
            "cost_usd": client.cost(r)}
    if r.guardrail_blocked:
        return None, {**meta, "reason": "guardrail"}
    text = r.text.strip()
    unknown = numbers_in(text) - numbers_in(content)
    if not text or unknown:
        return None, {**meta, "reason": "unfaithful", "unknown_numbers": sorted(unknown)[:5]}
    return text, {**meta, "reason": "ok"}
