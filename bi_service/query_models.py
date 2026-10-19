"""Request/response models. QuerySpec is the ONLY input the service accepts: there is no
field that carries SQL. Models are strict (extra fields rejected) so the JSON schema can be
handed to an LLM tool definition later without loosening validation."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FilterValue = str | bool | int | float | list[str] | list[int] | list[float]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Filter(_Strict):
    dimension: str = Field(max_length=64)
    op: Literal["eq", "ne", "in", "not_in"] = "eq"
    value: FilterValue


class DatePeriod(_Strict):
    """latest | month (YYYY-MM) | calendar_year | fiscal_year (Apr-Mar, named by end year) | range."""
    kind: Literal["latest", "month", "calendar_year", "fiscal_year", "range"] = "latest"
    month: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    year: int | None = Field(default=None, ge=2000, le=2100)
    start: date | None = None
    end: date | None = None


class Sort(_Strict):
    by: str = Field(max_length=64)   # a dimension id in the query, or "value"
    direction: Literal["asc", "desc"] = "asc"


class QuerySpec(_Strict):
    metric: str = Field(max_length=64)
    dimensions: list[str] = Field(default_factory=list, max_length=4)
    filters: list[Filter] = Field(default_factory=list, max_length=10)
    period: DatePeriod | None = None
    sort: list[Sort] = Field(default_factory=list, max_length=4)
    limit: int | None = Field(default=None, ge=1)
    include_total: bool = False   # overall row across the requested dimensions (where additive-safe)


class ErrorDetail(BaseModel):
    code: str
    message: str
    field: str | None = None


class MetricMetadata(BaseModel):
    id: str
    name: str
    behavior: str
    additivity: str
    format: dict
    definition: str


class ColumnMeta(BaseModel):
    name: str
    type: str
    role: Literal["dimension", "measure", "component"]


class QueryResult(BaseModel):
    status: Literal["success", "empty", "rejected", "failed", "timeout"]
    columns: list[ColumnMeta] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    total: list[Any] | None = None      # measure/component values of the overall row (same order as measure columns)
    snapshot_date: str | None = None    # latest month-end snapshot used (ISO date)
    metric: MetricMetadata | None = None
    period: dict | None = None
    query_id: str | None = None
    execution_time_ms: int | None = None
    bytes_scanned: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)
    sql: str | None = None
