"""Validate a QuerySpec against the semantic model and resolve it into a ValidatedQuery.

Everything the compiler needs is resolved here (metric, dimension objects, typed filter
values, concrete period dates), so the compiler never sees unchecked user input.
"""
import calendar
import re
from dataclasses import dataclass
from datetime import date

from .query_models import QuerySpec
from .semantic_loader import Dimension, Metric, SemanticModel

MAX_IN_VALUES = 50
MAX_STRING_LEN = 100
MAX_PERIOD_YEARS = 10
# Printable business text. No ';', quotes other than apostrophe, backslash, or comment markers.
SAFE_STRING = re.compile(r"[A-Za-z0-9 _.,&()/+:%'\-]+")   # used with fullmatch (no trailing-newline gap)


class QueryValidationError(ValueError):
    def __init__(self, errors: list[tuple[str, str, str | None]]):
        self.errors = errors  # (code, message, field)
        super().__init__("; ".join(f"{c}: {m}" for c, m, _ in errors))


@dataclass(frozen=True)
class ResolvedFilter:
    dimension: Dimension
    op: str
    values: tuple            # typed: str or bool


@dataclass(frozen=True)
class ResolvedPeriod:
    kind: str
    start: date | None       # None for 'latest'
    end: date | None

    def as_dict(self) -> dict:
        return {"kind": self.kind, "start": self.start.isoformat() if self.start else None,
                "end": self.end.isoformat() if self.end else None}


@dataclass(frozen=True)
class ResolvedSort:
    key: str                 # dimension id or "value"
    direction: str


@dataclass(frozen=True)
class ValidatedQuery:
    metric: Metric
    dimensions: tuple[Dimension, ...]
    filters: tuple[ResolvedFilter, ...]
    period: ResolvedPeriod
    sort: tuple[ResolvedSort, ...]
    limit: int


def _check_string(v, field: str, errors: list) -> str | None:
    if not isinstance(v, str) or isinstance(v, bool):
        errors.append(("invalid_filter_value", "expected a text value", field))
        return None
    if not v or len(v) > MAX_STRING_LEN:
        errors.append(("invalid_filter_value", f"text must be 1-{MAX_STRING_LEN} characters", field))
        return None
    if not SAFE_STRING.fullmatch(v) or "--" in v or "/*" in v:
        errors.append(("invalid_filter_value", "text contains disallowed characters", field))
        return None
    return v


def _resolve_period(spec_period, metric: Metric, errors: list) -> ResolvedPeriod:
    if spec_period is None:
        if metric.period_required:
            errors.append(("period_required", f"metric {metric.id} is a period flow; a period is required", "period"))
        return ResolvedPeriod("latest", None, None)
    p = spec_period
    k = p.kind
    expected = {"latest": set(), "month": {"month"}, "calendar_year": {"year"}, "fiscal_year": {"year"},
                "range": {"start", "end"}}[k]
    given = {f for f in ("month", "year", "start", "end") if getattr(p, f) is not None}
    if given != expected:
        errors.append(("invalid_period", f"period kind '{k}' requires exactly {sorted(expected) or 'no fields'}", "period"))
        return ResolvedPeriod(k, None, None)
    if k == "latest":
        return ResolvedPeriod(k, None, None)
    if k == "month":
        y, m = map(int, p.month.split("-"))
        return ResolvedPeriod(k, date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1]))
    if k == "calendar_year":
        return ResolvedPeriod(k, date(p.year, 1, 1), date(p.year, 12, 31))
    if k == "fiscal_year":
        return ResolvedPeriod(k, date(p.year - 1, 4, 1), date(p.year, 3, 31))
    if p.start > p.end:
        errors.append(("invalid_period", "start must be on or before end", "period"))
    elif (p.end - p.start).days > 366 * MAX_PERIOD_YEARS:
        errors.append(("invalid_period", f"range may span at most {MAX_PERIOD_YEARS} years", "period"))
    return ResolvedPeriod(k, p.start, p.end)


def validate(spec: QuerySpec, model: SemanticModel, *, default_limit: int, max_limit: int,
             allow_pending: bool = False) -> ValidatedQuery:
    errors: list[tuple[str, str, str | None]] = []

    metric = model.metrics.get(spec.metric)
    if metric is None:
        raise QueryValidationError([("unknown_metric", f"unknown metric '{spec.metric[:64]}'", "metric")])
    if not metric.approved and not allow_pending:
        raise QueryValidationError([("metric_not_approved", f"metric {metric.id} is pending approval", "metric")])

    dims: list[Dimension] = []
    for i, d in enumerate(spec.dimensions):
        f = f"dimensions[{i}]"
        dim = model.dimensions.get(d)
        if dim is None:
            errors.append(("unknown_dimension", f"unknown dimension '{d[:64]}'", f))
        elif d not in metric.allowed_dimensions or not dim.groupable:
            errors.append(("dimension_not_allowed", f"dimension {d} is not allowed for metric {metric.id}", f))
        elif dim in dims:
            errors.append(("duplicate_dimension", f"dimension {d} repeated", f))
        else:
            dims.append(dim)
    if sum(1 for d in dims if d.kind == "time") > 1:
        errors.append(("multiple_time_dimensions", "at most one time dimension per query", "dimensions"))

    filters: list[ResolvedFilter] = []
    filtered = set()
    for i, flt in enumerate(spec.filters):
        f = f"filters[{i}]"
        dim = model.dimensions.get(flt.dimension)
        if dim is None:
            errors.append(("unknown_dimension", f"unknown filter dimension '{flt.dimension[:64]}'", f))
            continue
        if not dim.filterable or flt.dimension not in metric.allowed_dimensions:
            errors.append(("filter_not_allowed", f"cannot filter on {dim.id}", f))
            continue
        if flt.op not in dim.operators:
            errors.append(("operator_not_allowed", f"operator {flt.op} not allowed on {dim.id}", f))
            continue
        raw = flt.value if isinstance(flt.value, list) else [flt.value]
        is_list_op = flt.op in ("in", "not_in")
        if is_list_op != isinstance(flt.value, list):
            errors.append(("invalid_filter_value", f"operator {flt.op} needs {'a list' if is_list_op else 'a single value'}", f))
            continue
        if not 1 <= len(raw) <= MAX_IN_VALUES:
            errors.append(("invalid_filter_value", f"1-{MAX_IN_VALUES} values required", f))
            continue
        if dim.type == "boolean":
            if not all(isinstance(v, bool) for v in raw):
                errors.append(("invalid_filter_value", "expected true or false", f))
                continue
            values = tuple(raw)
        else:
            values = tuple(_check_string(v, f, errors) for v in raw)
            if None in values:
                continue
        filters.append(ResolvedFilter(dim, flt.op, values))
        filtered.add(dim.id)
    for req in metric.required_filters:
        if req not in filtered:
            errors.append(("required_filter_missing", f"metric {metric.id} requires a filter on {req}", "filters"))

    period = _resolve_period(spec.period, metric, errors)

    sorts: list[ResolvedSort] = []
    dim_ids = {d.id for d in dims}
    for i, s in enumerate(spec.sort):
        if s.by != "value" and s.by not in dim_ids:
            errors.append(("invalid_sort", f"sort key '{s.by[:64]}' must be 'value' or a requested dimension", f"sort[{i}]"))
        else:
            sorts.append(ResolvedSort(s.by, s.direction))

    limit = spec.limit if spec.limit is not None else default_limit
    if limit > max_limit:
        errors.append(("limit_exceeded", f"limit may not exceed {max_limit}", "limit"))

    if errors:
        raise QueryValidationError(errors)
    return ValidatedQuery(metric, tuple(dims), tuple(filters), period, tuple(sorts), limit)
