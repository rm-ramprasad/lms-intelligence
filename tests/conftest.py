import itertools
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from bi_service.athena_executor import AthenaExecutor
from bi_service.config import ServiceConfig
from bi_service.query_service import QueryService
from bi_service.semantic_loader import load_semantic_model

ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real AWS Athena (run with -m live, needs approval)")


def pytest_collection_modifyitems(config, items):
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="live AWS test; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


class FakeAthena:
    """In-memory stand-in for the boto3 Athena client."""

    def __init__(self, *, states=("SUCCEEDED",), columns=(), rows=(), page_size=None,
                 start_error=None, results_error=None, reason=None):
        self.states = list(states)
        self.columns = list(columns)          # [(name, type)]
        self.rows = [list(r) for r in rows]   # string values or None
        self.page_size = page_size
        self.start_error = start_error
        self.results_error = results_error
        self.reason = reason
        self.calls = []
        self.stopped = []
        self._poll = 0

    def start_query_execution(self, **kw):
        self.calls.append(("start", kw))
        if self.start_error:
            raise ClientError({"Error": {"Code": self.start_error, "Message": "x"}}, "StartQueryExecution")
        return {"QueryExecutionId": "qid-1"}

    def get_query_execution(self, QueryExecutionId):
        state = self.states[min(self._poll, len(self.states) - 1)]
        self._poll += 1
        status = {"State": state}
        if self.reason:
            status["StateChangeReason"] = self.reason
        return {"QueryExecution": {"Status": status, "Statistics": {"DataScannedInBytes": 1234}}}

    def stop_query_execution(self, QueryExecutionId):
        self.stopped.append(QueryExecutionId)
        return {}

    def get_query_results(self, QueryExecutionId, MaxResults, NextToken=None):
        self.calls.append(("results", {"MaxResults": MaxResults, "NextToken": NextToken}))
        if self.results_error:
            raise ClientError({"Error": {"Code": self.results_error, "Message": "x"}}, "GetQueryResults")
        header = [{"VarCharValue": n} for n, _ in self.columns]
        body = [[{} if v is None else {"VarCharValue": v} for v in r] for r in self.rows]
        all_rows = [header] + body
        size = self.page_size or MaxResults
        start = int(NextToken or 0)
        chunk = all_rows[start:start + size]
        page = {"ResultSet": {"Rows": [{"Data": r} for r in chunk],
                              "ResultSetMetadata": {"ColumnInfo": [{"Name": n, "Type": t} for n, t in self.columns]}}}
        if start + size < len(all_rows):
            page["NextToken"] = str(start + size)
        return page


class FakeClock:
    def __init__(self, step=1.0):
        self.t = itertools.count(0, step)

    def __call__(self):
        return next(self.t)


@pytest.fixture(scope="session")
def model():
    return load_semantic_model()


@pytest.fixture(scope="session")
def config():
    return ServiceConfig(profile=None, region="ap-south-1", database="analytics_poc", workgroup="analytics_poc_wg",
                         timeout_seconds=5, poll_interval_seconds=0)


def make_service(model, config, fake: FakeAthena, *, timeout=5, allow_pending=False):
    ex = AthenaExecutor(fake, workgroup=config.workgroup, database=config.database, timeout_seconds=timeout,
                        poll_interval_seconds=0, clock=FakeClock(), sleep=lambda s: None)
    return QueryService(model, ex, config, allow_pending=allow_pending)


@pytest.fixture
def compile_only(model, config):
    svc = QueryService(model, None, config)
    return lambda spec: svc.compile(spec)
