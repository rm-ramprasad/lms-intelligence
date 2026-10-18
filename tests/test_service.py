import ast
import logging
from decimal import Decimal
from pathlib import Path

from conftest import FakeAthena, make_service

BRANCH_COLS = [("branch_code", "varchar"), ("value", "decimal")]


def test_valid_query_success(model, config):
    fake = FakeAthena(states=["QUEUED", "RUNNING", "SUCCEEDED"], columns=BRANCH_COLS,
                      rows=[["DEMO-EAST", "100.50"], ["DEMO-WEST", "99.00"]])
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "dimensions": ["branch_code"]})
    assert r.status == "success" and r.row_count == 2
    assert r.rows[0] == ["DEMO-EAST", Decimal("100.50")]
    assert [c.role for c in r.columns] == ["dimension", "measure"]
    assert r.metric.behavior == "snapshot" and r.metric.additivity == "semi_additive"
    assert r.query_id == "qid-1" and r.bytes_scanned == 1234 and r.execution_time_ms is not None
    start = fake.calls[0][1]
    assert start["WorkGroup"] == "analytics_poc_wg"
    assert start["QueryExecutionContext"]["Database"] == "analytics_poc"


def test_invalid_query_never_reaches_athena(model, config):
    fake = FakeAthena()
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "dimensions": ["account_number"]})
    assert r.status == "rejected" and r.errors[0].code == "unknown_dimension"
    assert fake.calls == []


def test_malformed_spec_rejected(model, config):
    fake = FakeAthena()
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "sql": "SELECT 1"})
    assert r.status == "rejected" and r.errors[0].code == "invalid_request"
    assert fake.calls == []


def test_ratio_result_and_zero_denominator(model, config):
    cols = [("product_code", "varchar"), ("numerator", "decimal"), ("denominator", "decimal"), ("value", "double")]
    fake = FakeAthena(columns=cols, rows=[["DF-RE", "25", "100", "0.25"], ["DF-GF", "0", "0", None]])
    r = make_service(model, config, fake).run({"metric": "gross_npa_ratio", "dimensions": ["product_code"]})
    assert r.status == "success"
    assert r.rows[0][3] == 0.25 and r.rows[1][3] is None
    assert any(w.startswith("zero_denominator: 1") for w in r.warnings)
    assert [c.role for c in r.columns] == ["dimension", "component", "component", "measure"]


def test_empty_result(model, config):
    fake = FakeAthena(columns=BRANCH_COLS, rows=[])
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "dimensions": ["branch_code"]})
    assert r.status == "empty" and r.row_count == 0


def test_empty_when_no_dimension_and_null_value(model, config):
    fake = FakeAthena(columns=[("value", "decimal")], rows=[[None]])
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "period": {"kind": "month", "month": "2019-01"}})
    assert r.status == "empty"


def test_athena_failed_state(model, config):
    fake = FakeAthena(states=["RUNNING", "FAILED"], reason="SYNTAX_ERROR: line 1")
    r = make_service(model, config, fake).run({"metric": "total_outstanding"})
    assert r.status == "failed" and r.errors[0].code == "athena_failed"


def test_athena_access_denied_on_start(model, config):
    fake = FakeAthena(start_error="AccessDeniedException")
    r = make_service(model, config, fake).run({"metric": "total_outstanding"})
    assert r.status == "failed" and r.errors[0].code == "AccessDeniedException"


def test_athena_error_while_fetching_results(model, config):
    fake = FakeAthena(columns=BRANCH_COLS, results_error="ThrottlingException")
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "dimensions": ["branch_code"]})
    assert r.status == "failed" and r.errors[0].code == "ThrottlingException"


def test_timeout_cancels_query(model, config):
    fake = FakeAthena(states=["RUNNING"])
    r = make_service(model, config, fake, timeout=3).run({"metric": "total_outstanding"})
    assert r.status == "timeout" and r.errors[0].code == "timeout"
    assert fake.stopped == ["qid-1"]


def test_row_limit_truncation(model, config):
    rows = [[f"B{i}", "1"] for i in range(6)]
    fake = FakeAthena(columns=BRANCH_COLS, rows=rows, page_size=2)
    r = make_service(model, config, fake).run({"metric": "total_outstanding", "dimensions": ["branch_code"], "limit": 5})
    assert r.row_count == 5 and r.truncated and "truncated" in r.warnings[0]
    assert all(kw["MaxResults"] <= 1000 for name, kw in fake.calls if name == "results")


def test_logs_contain_no_values_or_sql(model, config, caplog):
    fake = FakeAthena(columns=BRANCH_COLS, rows=[["SECRET-BRANCH", "123456.78"]])
    with caplog.at_level(logging.DEBUG, logger="bi_service"):
        make_service(model, config, fake).run({"metric": "total_outstanding", "dimensions": ["branch_code"],
                                               "filters": [{"dimension": "state_name", "op": "eq", "value": "Gujarat"}]})
    text = caplog.text
    assert "SECRET-BRANCH" not in text and "123456" not in text and "Gujarat" not in text and "SELECT" not in text
    assert "metric=total_outstanding" in text


def test_service_has_no_ui_or_llm_dependencies():
    banned = {"streamlit", "langchain", "anthropic", "bedrock", "openai"}
    pkg = Path(__file__).resolve().parents[1] / "bi_service"
    for f in pkg.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not names & banned, f.name
