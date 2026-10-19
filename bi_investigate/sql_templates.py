"""Governed SQL templates for Tier 2. Identifiers come only from the semantic model; dates are typed literals;
every statement passes bi_service's assert_safe_sql. Account-level joins happen inside Athena and only
aggregates are selected (no account identifiers in any result)."""
from datetime import date

from bi_service.semantic_loader import Metric, SemanticModel
from bi_service.sql_compiler import aggregate_sql, assert_safe_sql, ident, literal, row_value_sql

from .models import Comparison, Window

VIEW = "v_account_summary"
CTES = {"a", "b", "d", "r", "t", "m"}


class Templates:
    def __init__(self, model: SemanticModel):
        self.model = model
        v = model.views[VIEW]
        self.rel = v.qualified
        self.date = ident(v.snapshot_date_column)
        self.allowed = {x.qualified for x in model.views.values()} | CTES

    def _safe(self, sql: str) -> str:
        assert_safe_sql(sql, self.allowed)
        return sql

    def num_den(self, metric_id: str) -> tuple[str, str | None]:
        m = self.model.metrics[metric_id]
        if m.measure.aggregation == "ratio":
            return (aggregate_sql(self.model.metrics[m.measure.numerator]),
                    aggregate_sql(self.model.metrics[m.measure.denominator]))
        return aggregate_sql(m), None

    def window_pred(self, w: Window, flow: bool) -> str:
        if flow:
            return f"{self.date} BETWEEN {literal(w.start)} AND {literal(w.end)}"
        return f"{self.date} = {literal(w.end)}"

    # ---- T1: change + segment breakdown for both windows in one query (GROUPING SETS) -------------
    def segments(self, metric_id: str, c: Comparison, dims: list[str]) -> str:
        m: Metric = self.model.metrics[metric_id]
        flow = m.behavior == "period"
        num, den = self.num_den(metric_id)
        pa, pb = self.window_pred(c.a, flow), self.window_pred(c.b, flow)
        # Athena requires GROUPING SETS members to be column references: compute the window flag in a subquery.
        src = f"(SELECT *, CASE WHEN {pa} THEN 'A' ELSE 'B' END AS w FROM {self.rel} WHERE ({pa}) OR ({pb})) AS src"
        cols = [ident(self.model.dimensions[d].column) for d in dims]
        sets = ", ".join(["(w)"] + [f"(w, {col})" for col in cols])
        sel = ["w"] + [f"{col} AS {ident(d)}" for d, col in zip(dims, cols)]
        sel += [f"{num} AS num", f"{den or 'NULL'} AS den"]
        if cols:
            sel.append(f"grouping({', '.join(cols)}) AS g")
        return self._safe(f"SELECT {', '.join(sel)} FROM {src} GROUP BY GROUPING SETS ({sets})")

    # ---- T2: monthly series (for trend charts and unusualness) --------------------------------------
    def trend(self, metric_id: str, start: date, end: date) -> str:
        num, den = self.num_den(metric_id)
        return self._safe(f"SELECT {self.date} AS d, {num} AS num, {den or 'NULL'} AS den FROM {self.rel} "
                          f"WHERE {self.date} BETWEEN {literal(start)} AND {literal(end)} GROUP BY 1 ORDER BY 1")

    # ---- T3: account flows between two month-ends (aggregated) --------------------------------------
    def flows(self, date_a: date, date_b: date) -> str:
        npa_os = row_value_sql(self.model.metrics["npa_outstanding"])
        p90 = row_value_sql(self.model.metrics["par_90_plus_amount"])

        def cte(d: date) -> str:
            return (f"SELECT \"account_number\" AS acct, \"par_bucket\" AS bk, \"npa_flag\" AS npa, "
                    f"\"write_off_flag\" AS wo, \"ac_status\" AS st, {p90} AS p90, {npa_os} AS npaos "
                    f"FROM {self.rel} WHERE {self.date} = {literal(d)}")
        sql = (f"WITH a AS ({cte(date_a)}), b AS ({cte(date_b)}) "
               "SELECT CASE WHEN a.acct IS NULL THEN 'new' WHEN b.acct IS NULL THEN 'exited' "
               "WHEN COALESCE(b.wo, FALSE) AND NOT COALESCE(a.wo, FALSE) THEN 'written_off' "
               "WHEN b.st = 'Closed' AND COALESCE(a.st, '') <> 'Closed' THEN 'closed' ELSE 'continuing' END AS event, "
               "a.bk AS from_bucket, b.bk AS to_bucket, a.npa AS from_npa, b.npa AS to_npa, count(*) AS n, "
               "sum(COALESCE(a.p90, 0)) AS p90_a, sum(COALESCE(b.p90, 0)) AS p90_b, "
               "sum(COALESCE(a.npaos, 0)) AS npa_a, sum(COALESCE(b.npaos, 0)) AS npa_b "
               "FROM a FULL OUTER JOIN b ON a.acct = b.acct GROUP BY 1, 2, 3, 4, 5")
        return self._safe(sql)

    # ---- T4: monthly bucket transitions for the 12 months before date_b -----------------------------
    def monthly_transitions(self, start: date, end: date) -> str:
        sql = (f"WITH t AS (SELECT {self.date} AS d, \"par_bucket\" AS bk, "
               f"lag(\"par_bucket\") OVER (PARTITION BY \"account_number\" ORDER BY {self.date}) AS pb, "
               f"lag({self.date}) OVER (PARTITION BY \"account_number\" ORDER BY {self.date}) AS pd "
               f"FROM {self.rel} WHERE {self.date} BETWEEN {literal(start)} AND {literal(end)}) "
               "SELECT d, pb AS from_bucket, bk AS to_bucket, count(*) AS n FROM t "
               "WHERE pd = last_day_of_month(date_add('month', -1, d)) GROUP BY 1, 2, 3")
        return self._safe(sql)

    # ---- T5: concentration of account-level movement (counts only) ----------------------------------
    def concentration(self, metric_id: str, date_a: date, date_b: date) -> str:
        v = row_value_sql(self.model.metrics[metric_id])

        def cte(d: date) -> str:
            return f"SELECT \"account_number\" AS acct, {v} AS v FROM {self.rel} WHERE {self.date} = {literal(d)}"
        sql = (f"WITH a AS ({cte(date_a)}), b AS ({cte(date_b)}), "
               "d AS (SELECT abs(COALESCE(b.v, 0) - COALESCE(a.v, 0)) AS x FROM a FULL OUTER JOIN b ON a.acct = b.acct), "
               "r AS (SELECT x, sum(x) OVER (ORDER BY x DESC ROWS UNBOUNDED PRECEDING) AS cum, "
               "sum(x) OVER () AS tot, row_number() OVER (ORDER BY x DESC) AS rn FROM d WHERE x > 0) "
               "SELECT count(*) AS n_changed, max(tot) AS gross, "
               "min(CASE WHEN cum >= 0.5 * tot THEN rn END) AS n50, min(CASE WHEN cum >= 0.8 * tot THEN rn END) AS n80 FROM r")
        return self._safe(sql)

    def bounds(self) -> str:
        return self._safe(f"SELECT min({self.date}) AS first_date, max({self.date}) AS last_date FROM {self.rel}")
