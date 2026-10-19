"""Compile a ValidatedQuery into a single read-only Athena SELECT.

SQL is assembled only from:
  * fixed templates in this module,
  * identifiers taken from the semantic model (re-checked against an allowlist pattern),
  * literals built by `literal()` from already-typed values.
`assert_safe_sql()` is applied to every statement before it is returned.

Snapshot (semi-additive) metrics are restricted to the latest snapshot date in the period,
or to the latest snapshot per time bucket when a time dimension is requested, so balances
are never summed across months. Period (additive) metrics sum every month in the period.
"""
import re
from dataclasses import dataclass
from datetime import date

from .query_validator import ResolvedFilter, ValidatedQuery
from .semantic_loader import Condition, Dimension, Metric, SemanticModel

IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SNAPSHOT_CTE = "snapshot_dates"
SNAPSHOT_COL = "snapshot_date"     # MAX(snapshot date) per row; stripped from rows into QueryResult.snapshot_date
TOTAL_FLAG_COL = "is_total"        # grouping() flag; total row is moved into QueryResult.total
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|UNLOAD|CALL|EXECUTE|PREPARE|DEALLOCATE|USE|SET|MSCK|VACUUM|OPTIMIZE)\b",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class UnsafeSQLError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    value_columns: tuple[str, ...]     # measure columns in output order
    dimension_columns: tuple[str, ...]
    fetch_limit: int                   # limit + 1, used to detect truncation


def ident(name: str) -> str:
    if not IDENT.match(name):
        raise UnsafeSQLError(f"identifier not allowed: {name!r}")
    return f'"{name}"'


def literal(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    if isinstance(value, str):
        if any(ord(ch) < 32 for ch in value):
            raise UnsafeSQLError("control character in literal")
        return "'" + value.replace("'", "''") + "'"
    raise UnsafeSQLError(f"unsupported literal type {type(value).__name__}")


def assert_safe_sql(sql: str, allowed_relations: set[str]) -> None:
    stripped = STRING_LITERAL.sub("''", sql)
    if ";" in stripped or "--" in stripped or "/*" in stripped:
        raise UnsafeSQLError("multiple statements or comments are not allowed")
    if not re.match(r"^\s*(WITH|SELECT)\b", stripped, re.IGNORECASE):
        raise UnsafeSQLError("only SELECT statements are allowed")
    if FORBIDDEN.search(stripped):
        raise UnsafeSQLError("statement contains a forbidden keyword")
    for rel in re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.\"]*)", stripped, re.IGNORECASE):
        if rel.replace('"', "") not in allowed_relations:
            raise UnsafeSQLError(f"relation not allowed: {rel}")


def _time_expr(dim: Dimension) -> str:
    col = ident(dim.column)
    return {
        "month": f"date_trunc('month', {col})",
        "quarter": f"date_trunc('quarter', {col})",
        "year": f"year({col})",
        "fiscal_year": f"year(date_add('month', 9, {col}))",   # Apr-Mar, named by end year
    }[dim.grain]


def _dim_expr(dim: Dimension) -> str:
    return _time_expr(dim) if dim.kind == "time" else ident(dim.column)


def _condition_sql(c: Condition) -> str:
    op = {"eq": "=", "ne": "<>"}[c.op]
    return f"{ident(c.column)} {op} {literal(c.value)}"


def _aggregate(metric: Metric) -> str:
    m = metric.measure
    if m.aggregation == "sum":
        expr = " + ".join(f"COALESCE({ident(c)}, 0)" for c in m.columns)
    else:  # count_distinct
        expr = ident(m.columns[0])
    if m.condition:
        expr = f"CASE WHEN {_condition_sql(m.condition)} THEN {expr} END"
    return f"SUM({expr})" if m.aggregation == "sum" else f"COUNT(DISTINCT {expr})"


def aggregate_sql(metric: Metric) -> str:
    """Public: the governed aggregate expression for a non-ratio metric (used by the Tier 2 templates)."""
    return _aggregate(metric)


def row_value_sql(metric: Metric) -> str:
    """Public: per-row value whose SUM equals the metric (sum metrics only), e.g. for account-level deltas."""
    m = metric.measure
    if m.aggregation != "sum":
        raise UnsafeSQLError(f"row value only defined for sum metrics, not {m.aggregation}")
    expr = " + ".join(f"COALESCE({ident(c)}, 0)" for c in m.columns)
    return f"CASE WHEN {_condition_sql(m.condition)} THEN {expr} ELSE 0 END" if m.condition else expr


def _filter_sql(f: ResolvedFilter) -> str:
    col = ident(f.dimension.column)
    if f.op in ("eq", "ne"):
        return f"{col} {'=' if f.op == 'eq' else '<>'} {literal(f.values[0])}"
    vals = ", ".join(literal(v) for v in f.values)
    return f"{col} {'IN' if f.op == 'in' else 'NOT IN'} ({vals})"


def compile_latest_date(model: SemanticModel, view_name: str) -> str:
    """Fixed template: the latest snapshot date of a governed view (anchors relative periods)."""
    view = model.views[view_name]
    sql = f"SELECT MAX({ident(view.snapshot_date_column)}) AS \"{SNAPSHOT_COL}\" FROM {view.qualified}"
    assert_safe_sql(sql, {v.qualified for v in model.views.values()})
    return sql


def compile_query(q: ValidatedQuery, model: SemanticModel) -> CompiledQuery:
    metric = q.metric
    view = model.views[metric.view]
    relation = view.qualified
    date_col = ident(view.snapshot_date_column)

    # Athena requires GROUPING SETS members to be column references, so with a total row any time-grain
    # expression is first computed as a column ("_g<i>") in a subquery over the view.
    derived = {}
    if q.include_total:
        derived = {d.id: f"\"_g{i}\"" for i, d in enumerate(q.dimensions) if d.kind == "time"}
    if derived:
        extra = ", ".join(f"{_dim_expr(d)} AS {derived[d.id]}" for d in q.dimensions if d.id in derived)
        relation = f"(SELECT *, {extra} FROM {view.qualified}) AS src"

    select, group, dim_cols, dim_exprs = [], [], [], []
    for i, d in enumerate(q.dimensions, 1):
        expr = derived.get(d.id, _dim_expr(d))
        select.append(f"{expr} AS {ident(d.id)}")
        group.append(str(i))
        dim_cols.append(d.id)
        dim_exprs.append(expr)

    if metric.measure.aggregation == "ratio":
        num, den = model.metrics[metric.measure.numerator], model.metrics[metric.measure.denominator]
        select += [
            f"{_aggregate(num)} AS \"numerator\"",
            f"{_aggregate(den)} AS \"denominator\"",
            f"CAST({_aggregate(num)} AS double) / NULLIF(CAST({_aggregate(den)} AS double), 0) AS \"value\"",
        ]
        value_cols = ("numerator", "denominator", "value")
    else:
        select.append(f"{_aggregate(metric)} AS \"value\"")
        value_cols = ("value",)
    select.append(f"MAX({date_col}) AS \"{SNAPSHOT_COL}\"")
    if q.include_total:
        select.append(f"grouping({', '.join(dim_exprs)}) AS \"{TOTAL_FLAG_COL}\"")

    bounds = []
    if q.period.start is not None:
        bounds.append(f"{date_col} BETWEEN {literal(q.period.start)} AND {literal(q.period.end)}")

    cte = ""
    where = []
    time_dims = [d for d in q.dimensions if d.kind == "time"]
    if metric.behavior == "snapshot" or q.period.kind == "latest":
        # One snapshot per bucket (or overall). Filters on attributes do not move the snapshot date.
        bucket = ""
        if metric.behavior == "snapshot" and time_dims:
            bucket = f" GROUP BY {_time_expr(time_dims[0])}"
        cte_where = f" WHERE {' AND '.join(bounds)}" if bounds else ""
        cte = f"WITH {SNAPSHOT_CTE} AS (\n  SELECT MAX({date_col}) AS d FROM {view.qualified}{cte_where}{bucket}\n)\n"
        where.append(f"{date_col} IN (SELECT d FROM {SNAPSHOT_CTE})")
    else:
        where += bounds
    where += [_filter_sql(f) for f in q.filters]

    order = [f"\"{TOTAL_FLAG_COL}\" DESC"] if q.include_total else []   # total row first, never cut by LIMIT
    for s in q.sort:
        target = '"value"' if s.key == "value" else ident(s.key)
        order.append(f"{target} {s.direction.upper()}")
    if not q.sort:
        for d in q.dimensions:  # default: business order of the dimensions
            order.append(f"MIN({ident(d.sort_column)}) ASC" if d.sort_column else f"{ident(d.id)} ASC")

    sql = cte + "SELECT\n  " + ",\n  ".join(select) + f"\nFROM {relation}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if q.include_total:
        sql += f"\nGROUP BY GROUPING SETS (({', '.join(dim_exprs)}), ())"
    elif group:
        sql += "\nGROUP BY " + ", ".join(group)
    if order:
        sql += "\nORDER BY " + ", ".join(order)
    fetch = q.limit + 1 + (1 if q.include_total else 0)
    sql += f"\nLIMIT {int(fetch)}"

    allowed = {v.qualified for v in model.views.values()} | {SNAPSHOT_CTE}
    assert_safe_sql(sql, allowed)
    return CompiledQuery(sql, value_cols, tuple(dim_cols), fetch)
