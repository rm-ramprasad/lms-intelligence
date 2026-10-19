"""Template answers from a QueryResult. Every number in the text comes from the result."""
from bi_service.formatting import date_label, format_value, period_label
from bi_service.query_models import QueryResult
from bi_service.semantic_loader import SemanticModel

from .catalog import Catalog


def _fmt_parts(model: SemanticModel, metric_id: str):
    m = model.metrics[metric_id]
    if m.measure.aggregation == "ratio":
        return m.format, model.metrics[m.measure.numerator].format
    return m.format, None


def dimension_label(model: SemanticModel, dim_id: str, value) -> str:
    dim = model.dimensions.get(dim_id)
    if dim is None:
        return str(value)
    if dim.kind == "time":
        return period_label(value, dim.grain)
    if dim.type == "boolean":
        return {True: "Yes", False: "No"}.get(value, "—")
    return "—" if value is None else str(value)


def when_text(result: QueryResult, model: SemanticModel, metric_id: str) -> str:
    m = model.metrics[metric_id]
    if m.behavior == "snapshot":
        return f"at {date_label(result.snapshot_date)}" if result.snapshot_date else ""
    p = result.period or {}
    if p.get("kind") == "latest" and result.snapshot_date:
        return f"in {period_label(result.snapshot_date, 'month')}"
    if p.get("kind") == "fiscal_year" and p.get("start"):
        return f"in FY {int(p['end'][:4])}"
    if p.get("start") and p.get("end"):
        s, e = period_label(p["start"], "month"), period_label(p["end"], "month")
        return f"in {s}" if s == e else f"from {s} to {e}"
    return ""


def render_answer(result: QueryResult, spec: dict, model: SemanticModel, catalog: Catalog) -> str:
    if result.status == "empty":
        return "No data matched this question for the selected period and filters."
    if result.status != "success":
        msg = result.errors[0].message if result.errors else "the query did not complete"
        return f"Sorry, I couldn't get this answer ({result.status}: {msg})."

    metric_id = spec["metric"]
    name = catalog.kpis[metric_id].name if metric_id in catalog.kpis else metric_id
    vfmt, cfmt = _fmt_parts(model, metric_id)
    when = when_text(result, model, metric_id)
    dims = [c.name for c in result.columns if c.role == "dimension"]
    vi = len(result.columns) - 1                       # value is always the last column
    is_ratio = cfmt is not None

    def ratio_detail(num, den) -> str:
        return f" ({format_value(num, cfmt)} of {format_value(den, cfmt)})" if is_ratio else ""

    if not dims:
        row = result.rows[0]
        detail = ratio_detail(row[vi - 2], row[vi - 1]) if is_ratio else ""
        return f"**{name}** was **{format_value(row[vi], vfmt)}** {when}{detail}.".replace("  ", " ")

    lines = []
    first_dim = model.dimensions[dims[0]]
    if len(dims) == 1 and first_dim.kind == "time":
        pts = [(dimension_label(model, dims[0], r[0]), r[vi]) for r in result.rows]
        a, b = pts[0], pts[-1]
        lines.append(f"**{name}** {when}: {a[0]} **{format_value(a[1], vfmt)}** → {b[0]} **{format_value(b[1], vfmt)}** "
                     f"({len(pts)} periods).")
    else:
        valued = [r for r in result.rows if r[vi] is not None]
        by = ", ".join(model.dimensions[d].label.lower() for d in dims)
        if len(valued) == 1:
            r = valued[0]
            label = " / ".join(dimension_label(model, d, r[k]) for k, d in enumerate(dims))
            lines.append(f"**{name}** by {by} {when}: {label} **{format_value(r[vi], vfmt)}**.")
        elif valued:
            hi = max(valued, key=lambda r: r[vi])
            lo = min(valued, key=lambda r: r[vi])
            lab = lambda r: " / ".join(dimension_label(model, d, r[k]) for k, d in enumerate(dims))  # noqa: E731
            lines.append(f"**{name}** by {by} {when}. Highest: {lab(hi)} **{format_value(hi[vi], vfmt)}**; "
                         f"lowest: {lab(lo)} **{format_value(lo[vi], vfmt)}**.")
    if result.total is not None:
        tv = result.total[-1]
        detail = ratio_detail(result.total[0], result.total[1]) if is_ratio else ""
        lines.append(f"Overall: **{format_value(tv, vfmt)}**{detail}.")
    if result.truncated:
        lines.append(f"Showing the first {result.row_count} rows.")
    for w in result.warnings:
        if w.startswith("zero_denominator"):
            lines.append("Some groups have no outstanding, so their ratio is blank.")
    return " ".join(line.replace("  ", " ") for line in lines)
