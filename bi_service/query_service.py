"""QueryService: validate -> compile -> execute -> QueryResult.

Independent of any UI or LLM. Logs only metadata (metric, dimension ids, status, query id,
timings, bytes scanned, row count) — never SQL literals or result values.
"""
import logging
from typing import Any

from pydantic import ValidationError

from .athena_executor import AthenaExecutor
from .config import ServiceConfig
from .query_models import ColumnMeta, ErrorDetail, MetricMetadata, QueryResult, QuerySpec
from .query_validator import QueryValidationError, validate
from .semantic_loader import SemanticModel, load_semantic_model
from .sql_compiler import CompiledQuery, UnsafeSQLError, compile_query

log = logging.getLogger("bi_service")


class QueryService:
    def __init__(self, model: SemanticModel, executor: AthenaExecutor | None, config: ServiceConfig,
                 *, allow_pending: bool = False):
        self.model = model
        self.executor = executor
        self.config = config
        self.allow_pending = allow_pending

    @classmethod
    def from_defaults(cls) -> "QueryService":
        config = ServiceConfig.load()
        return cls(load_semantic_model(), AthenaExecutor.from_config(config), config)

    def _metric_meta(self, metric) -> MetricMetadata:
        return MetricMetadata(id=metric.id, name=metric.name, behavior=metric.behavior,
                              additivity=metric.additivity, format=metric.format, definition=metric.definition)

    def compile(self, spec: QuerySpec | dict[str, Any]):
        """Validate and compile without executing. Returns (ValidatedQuery, CompiledQuery)."""
        if not isinstance(spec, QuerySpec):
            spec = QuerySpec.model_validate(spec)
        vq = validate(spec, self.model, default_limit=self.config.default_limit,
                      max_limit=self.config.max_limit, allow_pending=self.allow_pending)
        return vq, compile_query(vq, self.model)

    def run(self, spec: QuerySpec | dict[str, Any]) -> QueryResult:
        try:
            vq, cq = self.compile(spec)
        except ValidationError as e:
            errs = [ErrorDetail(code="invalid_request", message=err["msg"],
                                field=".".join(str(p) for p in err["loc"]) or None) for err in e.errors()]
            return self._rejected(errs, spec)
        except QueryValidationError as e:
            return self._rejected([ErrorDetail(code=c, message=m, field=f) for c, m, f in e.errors], spec)
        except UnsafeSQLError as e:
            return self._rejected([ErrorDetail(code="unsafe_sql", message=str(e))], spec)

        if self.executor is None:
            raise RuntimeError("no executor configured")
        out = self.executor.execute(cq.sql, max_rows=cq.fetch_limit)
        result = self._to_result(vq, cq, out)
        log.info("query metric=%s dims=%s status=%s query_id=%s ms=%s bytes=%s rows=%s",
                 vq.metric.id, [d.id for d in vq.dimensions], result.status, result.query_id,
                 result.execution_time_ms, result.bytes_scanned, result.row_count)
        return result

    def _rejected(self, errors: list[ErrorDetail], spec) -> QueryResult:
        metric_id = spec.get("metric") if isinstance(spec, dict) else getattr(spec, "metric", None)
        log.info("query rejected metric=%s codes=%s", str(metric_id)[:64], [e.code for e in errors])
        return QueryResult(status="rejected", errors=errors)

    def _to_result(self, vq, cq: CompiledQuery, out) -> QueryResult:
        base = dict(metric=self._metric_meta(vq.metric), period=vq.period.as_dict(), query_id=out.query_id,
                    execution_time_ms=out.elapsed_ms, bytes_scanned=out.bytes_scanned, sql=cq.sql)
        if out.state == "TIMEOUT":
            return QueryResult(status="timeout", errors=[ErrorDetail(code="timeout", message=out.error_message)], **base)
        if out.state != "SUCCEEDED":
            return QueryResult(status="failed", errors=[ErrorDetail(code=out.error_code or "failed",
                                                                    message=out.error_message or "query failed")], **base)

        types = dict(out.columns)
        columns = [ColumnMeta(name=c, type=types.get(c, "varchar"), role="dimension") for c in cq.dimension_columns]
        columns += [ColumnMeta(name=c, type=types.get(c, "double"),
                               role="measure" if c == "value" else "component") for c in cq.value_columns]
        rows = out.rows
        truncated = len(rows) > vq.limit
        rows = rows[: vq.limit]
        warnings = []
        if truncated:
            warnings.append(f"result truncated to {vq.limit} rows")

        if "denominator" in cq.value_columns:
            idx = len(cq.dimension_columns) + 1
            zero = sum(1 for r in rows if r[idx] is not None and r[idx] == 0)
            if zero:
                warnings.append(f"zero_denominator: {zero} group(s) have a zero denominator; value is null")

        value_idx = len(cq.dimension_columns) + len(cq.value_columns) - 1
        all_null = all(r[value_idx] is None for r in rows)
        if not rows or (not cq.dimension_columns and all_null and not any("zero_denominator" in w for w in warnings)):
            return QueryResult(status="empty", columns=columns, rows=[], row_count=0, warnings=warnings, **base)
        return QueryResult(status="success", columns=columns, rows=rows, row_count=len(rows),
                           truncated=truncated, warnings=warnings, **base)
