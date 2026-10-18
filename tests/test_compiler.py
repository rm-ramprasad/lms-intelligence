import re

import pytest

from bi_service.query_validator import QueryValidationError
from bi_service.sql_compiler import UnsafeSQLError, assert_safe_sql, ident, literal

VIEW = "analytics_poc.v_account_summary"
ALLOWED = {VIEW, "snapshot_dates"}


def sql(compile_only, **spec):
    return compile_only(spec)[1].sql


def test_every_example_compiles_to_governed_view(model, compile_only):
    for ex in model.examples.values():
        s = sql(compile_only, **ex.spec)
        rels = set(re.findall(r"\bFROM\s+([\w.]+)", s))
        assert rels <= {VIEW, "snapshot_dates"}, ex.id
        assert s.lstrip().startswith(("WITH", "SELECT")) and ";" not in s


def test_snapshot_metric_uses_single_latest_snapshot(compile_only):
    s = sql(compile_only, metric="total_outstanding", dimensions=["state_name"])
    assert "MAX(\"transaction_date\") AS d FROM analytics_poc.v_account_summary\n)" in s
    assert "\"transaction_date\" IN (SELECT d FROM snapshot_dates)" in s
    assert "BETWEEN" not in s


def test_snapshot_metric_in_period_takes_latest_in_period(compile_only):
    s = sql(compile_only, metric="total_outstanding", period={"kind": "fiscal_year", "year": 2026})
    assert "WHERE \"transaction_date\" BETWEEN DATE '2025-04-01' AND DATE '2026-03-31'" in s.split(")\nSELECT")[0]
    assert "GROUP BY date_trunc" not in s


def test_snapshot_metric_with_time_dimension_takes_last_snapshot_per_bucket(compile_only):
    s = sql(compile_only, metric="total_outstanding", dimensions=["fiscal_year"],
            period={"kind": "range", "start": "2023-04-01", "end": "2026-03-31"})
    cte = s.split(")\nSELECT")[0]
    assert "GROUP BY year(date_add('month', 9, \"transaction_date\"))" in cte
    assert "IN (SELECT d FROM snapshot_dates)" in s


def test_period_metric_sums_across_months(compile_only):
    s = sql(compile_only, metric="disbursement_amount", dimensions=["year"], period={"kind": "fiscal_year", "year": 2026})
    assert "snapshot_dates" not in s
    assert "WHERE \"transaction_date\" BETWEEN DATE '2025-04-01' AND DATE '2026-03-31'" in s
    assert "SUM(COALESCE(\"disbursement_amount\", 0))" in s


def test_period_metric_latest_uses_latest_month(compile_only):
    s = sql(compile_only, metric="disbursement_amount", period={"kind": "latest"})
    assert "IN (SELECT d FROM snapshot_dates)" in s


def test_ratio_is_sum_over_sum_with_nullif(compile_only):
    s = sql(compile_only, metric="gross_npa_ratio", dimensions=["product_code"])
    assert ("CAST(SUM(CASE WHEN \"npa_flag\" = TRUE THEN COALESCE(\"total_os\", 0) END) AS double) / "
            "NULLIF(CAST(SUM(COALESCE(\"total_os\", 0)) AS double), 0) AS \"value\"") in s
    assert "AVG" not in s.upper()


def test_condition_and_filters(compile_only):
    s = sql(compile_only, metric="active_account_count",
            filters=[{"dimension": "npa_flag", "op": "eq", "value": False},
                     {"dimension": "state_name", "op": "not_in", "value": ["Delhi", "Gujarat"]}])
    assert "COUNT(DISTINCT CASE WHEN \"ac_status\" = 'Active' THEN \"account_number\" END)" in s
    assert "\"npa_flag\" = FALSE" in s and "\"state_name\" NOT IN ('Delhi', 'Gujarat')" in s


def test_default_sort_uses_business_order(compile_only):
    s = sql(compile_only, metric="total_outstanding", dimensions=["par_bucket"])
    assert "ORDER BY MIN(\"par_bucket_order\") ASC" in s


def test_limit_plus_one_for_truncation(compile_only):
    vq, cq = compile_only({"metric": "total_outstanding", "dimensions": ["branch_code"], "limit": 7})
    assert cq.sql.endswith("LIMIT 8") and cq.fetch_limit == 8


# ---- injection ---------------------------------------------------------------------------

INJECTIONS = [
    "x'; DROP TABLE analytics_poc.raw_dim_customers; --",
    "' OR '1'='1",
    "a' UNION SELECT first_name FROM analytics_poc.v_dim_customers --",
    "Gujarat/* comment */",
    "Gujarat\n",
    "Gujarat\\'",
]


@pytest.mark.parametrize("payload", INJECTIONS)
def test_injection_in_filter_value_rejected(compile_only, payload):
    with pytest.raises(QueryValidationError):
        compile_only({"metric": "total_outstanding", "filters": [{"dimension": "state_name", "op": "eq", "value": payload}]})


@pytest.mark.parametrize("field", ["metric", "dimensions", "sort"])
def test_injection_in_identifiers_rejected(compile_only, field):
    bad = "state_name; DROP TABLE x"
    spec = {"metric": "total_outstanding", "dimensions": ["state_name"]}
    if field == "metric":
        spec["metric"] = bad
    elif field == "dimensions":
        spec["dimensions"] = [bad]
    else:
        spec["sort"] = [{"by": bad}]
    with pytest.raises(QueryValidationError):
        compile_only(spec)


def test_apostrophe_is_escaped_not_rejected(compile_only):
    s = sql(compile_only, metric="total_outstanding", filters=[{"dimension": "branch_code", "op": "eq", "value": "O'Brien"}])
    assert "\"branch_code\" = 'O''Brien'" in s


def test_literal_and_ident_helpers():
    assert literal("a'b") == "'a''b'"
    assert literal(True) == "TRUE"
    with pytest.raises(UnsafeSQLError):
        literal(1.5)
    with pytest.raises(UnsafeSQLError):
        literal("a\x00")
    with pytest.raises(UnsafeSQLError):
        ident('x" OR 1=1')


@pytest.mark.parametrize("bad", [
    "SELECT 1; SELECT 2",
    "SELECT * FROM analytics_poc.v_account_summary -- x",
    "DROP TABLE analytics_poc.v_account_summary",
    "SELECT * FROM analytics_poc.raw_dim_customers",
    "SELECT * FROM analytics_poc.v_account_summary JOIN analytics_poc.v_dim_customers ON true",
    "WITH a AS (SELECT 1) INSERT INTO t SELECT * FROM a",
])
def test_guard_rejects_unsafe_sql(bad):
    with pytest.raises(UnsafeSQLError):
        assert_safe_sql(bad, ALLOWED)


def test_guard_ignores_keywords_inside_literals():
    assert_safe_sql("SELECT 1 FROM analytics_poc.v_account_summary WHERE \"x\" = 'Drop; -- it'", ALLOWED)
