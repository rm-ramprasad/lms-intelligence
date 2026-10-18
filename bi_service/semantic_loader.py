"""Load semantic/*.yaml into immutable models and cross-validate them.

Fails fast (SemanticConfigError) if any metric or dimension references a view that is not
governed, a column that does not exist in that view, an identifier/PII column used as a
dimension or summed, a placeholder-quality column, or an enabled relationship.
"""
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from .config import SEMANTIC_DIR

sys.path.insert(0, str(SEMANTIC_DIR.parent / "scripts"))
from catalog import EXCLUDED_PII, TYPES, resolved_columns  # noqa: E402

Behavior = Literal["snapshot", "period"]
TIME_GRAINS = ("month", "quarter", "year", "fiscal_year")
CONDITION_OPS = ("eq", "ne")


class SemanticConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Column:
    name: str
    sql_type: str          # Athena type, e.g. decimal(18,2), varchar, boolean, date
    pii: str
    quality: str | None


@dataclass(frozen=True)
class View:
    name: str              # e.g. v_account_summary
    qualified: str         # e.g. analytics_poc.v_account_summary
    snapshot_date_column: str
    columns: dict[str, Column]


@dataclass(frozen=True)
class Condition:
    column: str
    op: str
    value: Any


@dataclass(frozen=True)
class Measure:
    aggregation: Literal["sum", "count_distinct", "ratio"]
    columns: tuple[str, ...] = ()
    condition: Condition | None = None
    numerator: str | None = None
    denominator: str | None = None


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    view: str
    measure: Measure
    behavior: Behavior
    additivity: str
    period_required: bool
    required_filters: tuple[str, ...]
    allowed_dimensions: tuple[str, ...]
    format: dict
    definition: str
    status: str
    validation_sql: str

    @property
    def approved(self) -> bool:
        return self.status == "approved"


@dataclass(frozen=True)
class Dimension:
    id: str
    label: str
    kind: Literal["attribute", "time"]
    view: str
    column: str
    groupable: bool
    filterable: bool
    type: str = "string"
    grain: str | None = None
    sort_column: str | None = None
    operators: tuple[str, ...] = ()


@dataclass(frozen=True)
class Example:
    id: str
    question: str
    spec: dict
    reference_sql: str


@dataclass(frozen=True)
class SemanticModel:
    database: str
    views: dict[str, View]
    metrics: dict[str, Metric]
    dimensions: dict[str, Dimension]
    examples: dict[str, Example] = field(default_factory=dict)


def _read(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _views(tables_yaml: dict) -> dict[str, View]:
    db = tables_yaml["aws"]["database"]
    out = {}
    for tname, t in tables_yaml["tables"].items():
        if t.get("query_access") != "governed":
            continue
        name = f"v_{tname}"
        cols = {c["name"]: Column(c["name"], TYPES[c["type"]], c["pii"], c["quality"]) for c in resolved_columns(t)}
        date_col = t.get("snapshot_date_column")
        if date_col not in cols:
            raise SemanticConfigError(f"{name}: snapshot_date_column {date_col!r} missing")
        out[name] = View(name, f"{db}.{name}", date_col, cols)
    if not out:
        raise SemanticConfigError("no governed views in tables.yaml")
    return out


def _col(views: dict[str, View], view: str, column: str, ctx: str) -> Column:
    if view not in views:
        raise SemanticConfigError(f"{ctx}: view {view!r} is not governed")
    if column not in views[view].columns:
        raise SemanticConfigError(f"{ctx}: column {column!r} not in {view}")
    c = views[view].columns[column]
    if c.pii in EXCLUDED_PII:
        raise SemanticConfigError(f"{ctx}: column {column!r} is PII ({c.pii})")
    if c.quality:
        raise SemanticConfigError(f"{ctx}: column {column!r} has quality issue ({c.quality})")
    return c


def _dimensions(raw: dict, views: dict[str, View]) -> dict[str, Dimension]:
    out = {}
    for d in raw["dimensions"]:
        dim = Dimension(**{**d, "operators": tuple(d.get("operators", ()))})
        ctx = f"dimension {dim.id}"
        if dim.id in out:
            raise SemanticConfigError(f"{ctx}: duplicate id")
        c = _col(views, dim.view, dim.column, ctx)
        if dim.kind == "time":
            if dim.grain not in TIME_GRAINS or dim.filterable or dim.column != views[dim.view].snapshot_date_column:
                raise SemanticConfigError(f"{ctx}: time dimensions need a valid grain on the snapshot date and filterable=false")
        else:
            if c.pii == "identifier":
                raise SemanticConfigError(f"{ctx}: identifier column cannot be a dimension")
            expected = "boolean" if dim.type == "boolean" else "varchar"
            if c.sql_type != expected:
                raise SemanticConfigError(f"{ctx}: type {dim.type} does not match column type {c.sql_type}")
            if dim.filterable and not dim.operators:
                raise SemanticConfigError(f"{ctx}: filterable dimension needs operators")
            if set(dim.operators) - {"eq", "ne", "in", "not_in"}:
                raise SemanticConfigError(f"{ctx}: unknown operator")
            if dim.type == "boolean" and set(dim.operators) != {"eq"}:
                raise SemanticConfigError(f"{ctx}: boolean dimensions support eq only")
            if dim.sort_column:
                _col(views, dim.view, dim.sort_column, ctx)
        out[dim.id] = dim
    return out


def _metrics(raw: dict, views: dict[str, View], dims: dict[str, Dimension]) -> dict[str, Metric]:
    out = {}
    for m in raw["metrics"]:
        ctx = f"metric {m['id']}"
        if m["id"] in out:
            raise SemanticConfigError(f"{ctx}: duplicate id")
        ms = m["measure"]
        cond = Condition(**ms["condition"]) if ms.get("condition") else None
        measure = Measure(ms["aggregation"], tuple(ms.get("columns", ())), cond, ms.get("numerator"), ms.get("denominator"))
        allowed = m["allowed_dimensions"]
        allowed = tuple(d for d in dims if dims[d].view == m["view"]) if allowed == "all" else tuple(allowed)
        metric = Metric(
            id=m["id"], name=m["name"], view=m["view"], measure=measure, behavior=m["behavior"],
            additivity=m["additivity"], period_required=m["period_required"],
            required_filters=tuple(m.get("required_filters", ())), allowed_dimensions=allowed,
            format=m["format"], definition=m["definition"], status=m["status"], validation_sql=m["validation_sql"],
        )
        if metric.view not in views:
            raise SemanticConfigError(f"{ctx}: view {metric.view!r} is not governed")
        if metric.behavior not in ("snapshot", "period"):
            raise SemanticConfigError(f"{ctx}: behavior must be snapshot or period")
        if metric.status not in ("approved", "pending_approval"):
            raise SemanticConfigError(f"{ctx}: unknown status")
        expected_add = {"snapshot": "semi_additive", "period": "additive"}[metric.behavior]
        if measure.aggregation == "ratio":
            expected_add = "non_additive"
        if metric.additivity != expected_add:
            raise SemanticConfigError(f"{ctx}: additivity must be {expected_add} for {measure.aggregation}/{metric.behavior}")
        for d in allowed + metric.required_filters:
            if d not in dims or dims[d].view != metric.view:
                raise SemanticConfigError(f"{ctx}: unknown dimension {d!r}")
        if measure.aggregation == "sum":
            if not measure.columns:
                raise SemanticConfigError(f"{ctx}: sum needs columns")
            for c in measure.columns:
                col = _col(views, metric.view, c, ctx)
                if not col.sql_type.startswith("decimal"):
                    raise SemanticConfigError(f"{ctx}: cannot sum non-numeric column {c!r}")
        elif measure.aggregation == "count_distinct":
            if len(measure.columns) != 1:
                raise SemanticConfigError(f"{ctx}: count_distinct needs exactly one column")
            _col(views, metric.view, measure.columns[0], ctx)
        elif measure.aggregation != "ratio":
            raise SemanticConfigError(f"{ctx}: unknown aggregation")
        if cond:
            c = _col(views, metric.view, cond.column, ctx)
            if cond.op not in CONDITION_OPS or c.pii == "identifier":
                raise SemanticConfigError(f"{ctx}: invalid condition")
        out[metric.id] = metric

    for metric in out.values():  # ratio components resolved after all metrics are loaded
        ms = metric.measure
        if ms.aggregation != "ratio":
            continue
        for part in (ms.numerator, ms.denominator):
            comp = out.get(part)
            if comp is None or comp.measure.aggregation == "ratio":
                raise SemanticConfigError(f"metric {metric.id}: ratio component {part!r} must be a non-ratio metric")
            if comp.view != metric.view or comp.behavior != metric.behavior:
                raise SemanticConfigError(f"metric {metric.id}: component {part!r} must share view and behavior")
            if metric.approved and not comp.approved:
                raise SemanticConfigError(f"metric {metric.id}: approved ratio uses unapproved component {part!r}")
    return out


def load_semantic_model(semantic_dir: Path = SEMANTIC_DIR) -> SemanticModel:
    tables = _read(semantic_dir / "tables.yaml")
    views = _views(tables)
    dims = _dimensions(_read(semantic_dir / "dimensions.yaml"), views)
    metrics = _metrics(_read(semantic_dir / "metrics.yaml"), views, dims)
    rels = _read(semantic_dir / "relationships.yaml").get("relationships", [])
    if any(r.get("enabled") for r in rels):
        raise SemanticConfigError("relationships are not supported in V0.1; all must be enabled: false")
    examples = {e["id"]: Example(**e) for e in _read(semantic_dir / "examples.yaml").get("examples", [])}
    return SemanticModel(tables["aws"]["database"], views, metrics, dims, examples)
