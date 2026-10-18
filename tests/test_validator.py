from datetime import date

import pytest
from pydantic import ValidationError

from bi_service.query_models import QuerySpec
from bi_service.query_validator import QueryValidationError, validate


def v(model, **spec):
    return validate(QuerySpec(**spec), model, default_limit=100, max_limit=1000)


def codes(model, **spec):
    with pytest.raises(QueryValidationError) as e:
        v(model, **spec)
    return {c for c, _, _ in e.value.errors}


def test_valid_query_resolves(model):
    q = v(model, metric="total_outstanding", dimensions=["state_name"],
          filters=[{"dimension": "loan_group", "op": "in", "value": ["Growth Finance", "Rural Enterprise"]}])
    assert q.metric.id == "total_outstanding"
    assert q.period.kind == "latest" and q.limit == 100


def test_invalid_metric(model):
    assert codes(model, metric="total_profit") == {"unknown_metric"}


def test_pending_metric_rejected_unless_allowed(model):
    assert codes(model, metric="write_off_outstanding") == {"metric_not_approved"}
    q = validate(QuerySpec(metric="write_off_outstanding"), model, default_limit=10, max_limit=10, allow_pending=True)
    assert q.metric.id == "write_off_outstanding"


@pytest.mark.parametrize("dim, code", [
    ("account_number", "unknown_dimension"),
    ("customer_id", "unknown_dimension"),
    ("first_name", "unknown_dimension"),
    ("vertical", "unknown_dimension"),
])
def test_unauthorized_dimensions(model, dim, code):
    assert codes(model, metric="total_outstanding", dimensions=[dim]) == {code}


def test_duplicate_and_multiple_time_dimensions(model):
    assert "duplicate_dimension" in codes(model, metric="total_outstanding", dimensions=["state_name", "state_name"])
    assert "multiple_time_dimensions" in codes(model, metric="total_outstanding", dimensions=["month", "year"])


@pytest.mark.parametrize("flt, code", [
    ({"dimension": "month", "op": "eq", "value": "2026-03"}, "filter_not_allowed"),
    ({"dimension": "npa_flag", "op": "in", "value": [True]}, "operator_not_allowed"),
    ({"dimension": "npa_flag", "op": "eq", "value": "yes"}, "invalid_filter_value"),
    ({"dimension": "state_name", "op": "eq", "value": ["Gujarat"]}, "invalid_filter_value"),
    ({"dimension": "state_name", "op": "in", "value": "Gujarat"}, "invalid_filter_value"),
    ({"dimension": "state_name", "op": "in", "value": []}, "invalid_filter_value"),
    ({"dimension": "state_name", "op": "eq", "value": "x" * 101}, "invalid_filter_value"),
    ({"dimension": "state_name", "op": "eq", "value": 5}, "invalid_filter_value"),
    ({"dimension": "ssn", "op": "eq", "value": "1"}, "unknown_dimension"),
])
def test_invalid_filters(model, flt, code):
    assert code in codes(model, metric="total_outstanding", filters=[flt])


def test_filter_model_rejects_unknown_operator():
    with pytest.raises(ValidationError):
        QuerySpec(metric="total_outstanding", filters=[{"dimension": "state_name", "op": "like", "value": "%"}])


def test_spec_rejects_extra_fields_like_sql():
    with pytest.raises(ValidationError):
        QuerySpec(metric="total_outstanding", sql="SELECT 1")


def test_period_required_for_flow_metrics(model):
    assert codes(model, metric="disbursement_amount") == {"period_required"}


@pytest.mark.parametrize("period, start, end", [
    ({"kind": "month", "month": "2026-02"}, date(2026, 2, 1), date(2026, 2, 28)),
    ({"kind": "fiscal_year", "year": 2026}, date(2025, 4, 1), date(2026, 3, 31)),
    ({"kind": "calendar_year", "year": 2025}, date(2025, 1, 1), date(2025, 12, 31)),
    ({"kind": "range", "start": "2025-06-01", "end": "2025-09-30"}, date(2025, 6, 1), date(2025, 9, 30)),
])
def test_period_resolution(model, period, start, end):
    q = v(model, metric="disbursement_amount", period=period)
    assert (q.period.start, q.period.end) == (start, end)


@pytest.mark.parametrize("period", [
    {"kind": "month"},
    {"kind": "fiscal_year", "month": "2026-01"},
    {"kind": "range", "start": "2026-03-01", "end": "2026-01-01"},
    {"kind": "range", "start": "2000-01-01", "end": "2026-01-01"},
])
def test_invalid_periods(model, period):
    assert codes(model, metric="total_outstanding", period=period) == {"invalid_period"}


def test_sort_and_limit_rules(model):
    assert "invalid_sort" in codes(model, metric="total_outstanding", dimensions=["state_name"], sort=[{"by": "branch_code"}])
    assert "limit_exceeded" in codes(model, metric="total_outstanding", limit=5000)


def test_errors_are_collected(model):
    c = codes(model, metric="total_outstanding", dimensions=["nope"], limit=5000,
              filters=[{"dimension": "state_name", "op": "eq", "value": "a;b"}])
    assert {"unknown_dimension", "limit_exceeded", "invalid_filter_value"} <= c
