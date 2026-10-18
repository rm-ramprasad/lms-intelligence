"""Execute one compiled SELECT in the approved Athena workgroup.

* Workgroup and database are fixed at construction; the workgroup enforces the results
  location, encryption and the per-query scan cutoff server-side.
* Client-side timeout: when exceeded the query is cancelled with StopQueryExecution.
* At most `max_rows` result rows are fetched.
* Never logs SQL text or result values.
"""
import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger("bi_service.athena")
TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED"}
PAGE_SIZE = 1000


@dataclass
class ExecutionOutcome:
    state: str                         # SUCCEEDED | FAILED | CANCELLED | TIMEOUT | ERROR
    query_id: str | None = None
    columns: list[tuple[str, str]] = field(default_factory=list)   # (name, athena type)
    rows: list[list[Any]] = field(default_factory=list)
    bytes_scanned: int | None = None
    elapsed_ms: int = 0
    error_code: str | None = None
    error_message: str | None = None


def _convert(value: str | None, athena_type: str):
    if value is None:
        return None
    t = athena_type.lower()
    if t in ("integer", "bigint", "smallint", "tinyint"):
        return int(value)
    if t == "decimal":
        return Decimal(value)
    if t in ("double", "real", "float"):
        return float(value)
    if t == "boolean":
        return value.lower() == "true"
    return value  # varchar, date (ISO string), timestamp


class AthenaExecutor:
    def __init__(self, client, *, workgroup: str, database: str, timeout_seconds: float = 60.0,
                 poll_interval_seconds: float = 0.5, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.workgroup = workgroup
        self.database = database
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleep = sleep

    @classmethod
    def from_config(cls, config) -> "AthenaExecutor":
        import boto3
        session = boto3.Session(profile_name=config.profile, region_name=config.region)
        return cls(session.client("athena"), workgroup=config.workgroup, database=config.database,
                   timeout_seconds=config.timeout_seconds, poll_interval_seconds=config.poll_interval_seconds)

    def execute(self, sql: str, max_rows: int) -> ExecutionOutcome:
        started = self._clock()
        elapsed = lambda: int((self._clock() - started) * 1000)  # noqa: E731
        try:
            qid = self.client.start_query_execution(
                QueryString=sql,
                WorkGroup=self.workgroup,
                QueryExecutionContext={"Catalog": "AwsDataCatalog", "Database": self.database},
            )["QueryExecutionId"]
        except (ClientError, BotoCoreError) as e:
            code = e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__
            log.warning("athena start failed code=%s", code)
            return ExecutionOutcome("ERROR", error_code=code, error_message="could not start query", elapsed_ms=elapsed())

        deadline = started + self.timeout_seconds
        try:
            while True:
                q = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
                state = q["Status"]["State"]
                if state in TERMINAL:
                    break
                if self._clock() >= deadline:
                    self.client.stop_query_execution(QueryExecutionId=qid)
                    log.warning("athena query timed out query_id=%s", qid)
                    return ExecutionOutcome("TIMEOUT", qid, elapsed_ms=elapsed(), error_code="timeout",
                                            error_message=f"query exceeded {self.timeout_seconds:g}s and was cancelled")
                self._sleep(self.poll_interval_seconds)

            scanned = q.get("Statistics", {}).get("DataScannedInBytes")
            if state != "SUCCEEDED":
                log.warning("athena query %s query_id=%s", state.lower(), qid)
                return ExecutionOutcome(state, qid, bytes_scanned=scanned, elapsed_ms=elapsed(),
                                        error_code=f"athena_{state.lower()}",
                                        error_message=q["Status"].get("StateChangeReason") or "query did not succeed")

            columns, rows, token, header_skipped = [], [], None, False
            while len(rows) < max_rows:
                kwargs = {"QueryExecutionId": qid, "MaxResults": min(PAGE_SIZE, max_rows + 1)}
                if token:
                    kwargs["NextToken"] = token
                page = self.client.get_query_results(**kwargs)
                if not columns:
                    columns = [(c["Name"], c["Type"]) for c in page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]]
                data = page["ResultSet"]["Rows"]
                if not header_skipped and data:
                    data, header_skipped = data[1:], True
                for r in data:
                    vals = [d.get("VarCharValue") for d in r["Data"]]
                    rows.append([_convert(v, t) for v, (_, t) in zip(vals, columns)])
                token = page.get("NextToken")
                if not token:
                    break
            return ExecutionOutcome("SUCCEEDED", qid, columns, rows[:max_rows], scanned, elapsed())
        except (ClientError, BotoCoreError) as e:
            code = e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__
            log.warning("athena call failed query_id=%s code=%s", qid, code)
            return ExecutionOutcome("ERROR", qid, error_code=code, error_message="Athena API call failed", elapsed_ms=elapsed())
