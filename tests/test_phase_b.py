"""Milestone 3 Phase B: totals, snapshot date, formatting, latest-date lookup."""
from datetime import date
from decimal import Decimal

import pytest

from bi_service.formatting import date_label, fiscal_quarter, format_value, period_label
from bi_service.query_validator import QueryValidationError
from conftest import FakeAthena, make_service


def test_total_uses_grouping_sets_and_sorts_total_first(compile_only):
    _, cq = compile_only({"metric": "gross_npa_ratio", "dimensions": ["state_name"], "include_total": True, "limit": 5})
    assert 'GROUP BY GROUPING SETS (("state_name"), ())' in cq.sql
    assert 'grouping("state_name") AS "is_total"' in cq.sql
    assert 'ORDER BY "is_total" DESC' in cq.sql
    assert cq.fetch_limit == 7


def test_total_ignored_without_dimensions(compile_only):
    vq, cq = compile_only({"metric": "total_outstanding", "include_total": True})
    assert not vq.include_total and "GROUPING SETS" not in cq.sql


def test_total_across_months_rejected_for_snapshot(compile_only):
    with pytest.raises(QueryValidationError) as e:
        compile_only({"metric": "total_outstanding", "dimensions": ["month"], "include_total": True,
                      "period": {"kind": "fiscal_year", "year": 2026}})
    assert e.value.errors[0][0] == "total_not_meaningful"


def test_total_across_months_allowed_for_flows(compile_only):
    vq, _ = compile_only({"metric": "disbursement_amount", "dimensions": ["month"], "include_total": True,
                          "period": {"kind": "fiscal_year", "year": 2026}})
    assert vq.include_total


def test_snapshot_date_column_always_present(compile_only):
    _, cq = compile_only({"metric": "disbursement_amount", "period": {"kind": "latest"}})
    assert 'MAX("transaction_date") AS "snapshot_date"' in cq.sql


def test_service_splits_total_row_and_snapshot_date(model, config):
    cols = [("state_name", "varchar"), ("numerator", "decimal"), ("denominator", "decimal"), ("value", "double"),
            ("snapshot_date", "date"), ("is_total", "integer")]
    rows = [[None, "30", "1000", "0.03", "2026-03-31", "1"],
            ["Tamil Nadu", "20", "200", "0.1", "2026-03-31", "0"],
            ["Delhi", "10", "800", "0.0125", "2026-03-31", "0"]]
    r = make_service(model, config, FakeAthena(columns=cols, rows=rows)).run(
        {"metric": "gross_npa_ratio", "dimensions": ["state_name"], "include_total": True})
    assert r.status == "success" and r.row_count == 2
    assert r.total == [Decimal("30"), Decimal("1000"), 0.03]
    assert r.snapshot_date == "2026-03-31"
    assert all(len(row) == 4 for row in r.rows)
    assert [c.name for c in r.columns] == ["state_name", "numerator", "denominator", "value"]


def test_latest_snapshot_date(model, config):
    svc = make_service(model, config, FakeAthena(columns=[("snapshot_date", "date")], rows=[["2026-03-31"]]))
    assert svc.latest_snapshot_date() == date(2026, 3, 31)
    svc = make_service(model, config, FakeAthena(states=["FAILED"]))
    assert svc.latest_snapshot_date() is None


@pytest.mark.parametrize("value, fmt, expected", [
    (Decimal("20341000000.5"), {"type": "currency"}, "₹2,034.10 Cr"),
    (Decimal("5500000"), {"type": "currency"}, "₹55.00 L"),
    (0.0262, {"type": "percent", "decimals": 2}, "2.62%"),
    (27570, {"type": "integer"}, "27,570"),
    (None, {"type": "currency"}, "—"),
])
def test_format_value(value, fmt, expected):
    assert format_value(value, fmt) == expected


@pytest.mark.parametrize("value, grain, expected", [
    ("2026-03-01", "month", "Mar 2026"),
    ("2025-04-01", "quarter", "Q1 FY26"),
    ("2026-01-01", "quarter", "Q4 FY26"),
    (2026, "fiscal_year", "FY 2026"),
    (2025, "year", "2025"),
])
def test_period_label(value, grain, expected):
    assert period_label(value, grain) == expected


def test_fiscal_quarter_edges():
    assert fiscal_quarter(date(2025, 3, 31)) == (4, 2025)
    assert fiscal_quarter(date(2025, 4, 1)) == (1, 2026)
    assert date_label("2026-03-31") == "31 Mar 2026"
