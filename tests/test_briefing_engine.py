"""Tier 2 engine, templates, playbook and store. Fake runner with planted data; no AWS."""
import re
import shutil
from datetime import date

import pytest
import yaml

from bi_investigate.engine import BriefingEngine, RunError
from bi_investigate.models import Briefing
from bi_investigate.playbook import PlaybookError, load_playbook
from bi_investigate.sql_templates import Templates
from bi_investigate.store import StoreError, approve, list_briefings, load, save
from bi_investigate.windows import comparison
from bi_service.sql_compiler import UnsafeSQLError
from conftest import ROOT

AS_OF = date(2026, 3, 31)


class FakeRunner:
    """Recognises each governed template by shape and returns planted aggregate rows."""

    def __init__(self, fail_on: str | None = None):
        self.count = 0
        self.bytes = 0
        self.sqls: list[str] = []
        self.fail_on = fail_on

    def run(self, sql: str):
        self.sqls.append(sql)
        self.count += 1
        if self.fail_on and self.fail_on in sql:
            raise RunError("FAILED: test")
        if "first_date" in sql:
            return [{"first_date": "2022-04-30", "last_date": "2026-03-31"}]
        if "GROUPING SETS" in sql:
            dims = [d for _, d in re.findall(r'"(\w+)" AS "(\w+)"', sql)]
            n = len(dims)
            rows = [{"w": "A", "num": 20.0, "den": 1000.0, "g": (1 << n) - 1, **{d: None for d in dims}},
                    {"w": "B", "num": 40.0, "den": 1000.0, "g": (1 << n) - 1, **{d: None for d in dims}}]
            for i, d in enumerate(dims):
                g = ((1 << n) - 1) ^ (1 << (n - 1 - i))
                for w, s1, s2 in (("A", 10.0, 10.0), ("B", 30.0, 10.0)):
                    rows.append({"w": w, "num": s1, "den": 500.0, "g": g, **{x: None for x in dims}, d: "Seg-1"})
                    rows.append({"w": w, "num": s2, "den": 500.0, "g": g, **{x: None for x in dims}, d: "Seg-2"})
            return rows
        if "n_changed" in sql:
            return [{"n_changed": 40, "gross": 500.0, "n50": 3, "n80": 9}]
        if "lag(" in sql:
            out = []
            for m in range(1, 13):
                d = f"2025-{m:02d}-28"
                out += [{"d": d, "from_bucket": "61-90 DPD", "to_bucket": "90+ DPD", "n": 2},
                        {"d": d, "from_bucket": "61-90 DPD", "to_bucket": "61-90 DPD", "n": 8}]
            return out
        if "FULL OUTER JOIN b" in sql and "event" in sql:
            def r(ev, fb, tb, n, a, b, fn, tn):
                return {"event": ev, "from_bucket": fb, "to_bucket": tb, "from_npa": fn, "to_npa": tn, "n": n,
                        "p90_a": a, "p90_b": b, "npa_a": a if fn else 0.0, "npa_b": b if tn else 0.0}
            return [r("continuing", "90+ DPD", "90+ DPD", 10, 1000.0, 1050.0, True, True),
                    r("continuing", "61-90 DPD", "90+ DPD", 6, 0.0, 600.0, False, True),
                    r("continuing", "61-90 DPD", "61-90 DPD", 4, 0.0, 0.0, False, False),
                    r("continuing", "90+ DPD", "Current", 1, 100.0, 0.0, True, False),
                    r("continuing", "Current", "Current", 400, 0.0, 0.0, False, False)]
        if "ORDER BY 1" in sql:   # trend: steady, then a jump in the last month
            out, d = [], date(2024, 3, 31)
            from bi_investigate.windows import shift_months
            for i in range(25):
                out.append({"d": d.isoformat(), "num": 20.0 + (i % 2) * 0.5 + (20 if i == 24 else 0), "den": 1000.0})
                d = shift_months(d, 1)
            return out
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


@pytest.fixture(scope="module")
def playbook(model):
    return load_playbook(model)


@pytest.fixture
def briefing(model, playbook):
    return BriefingEngine(model, playbook, FakeRunner(), workers=1).build(AS_OF)


# ---- playbook -------------------------------------------------------------------------------------

def test_playbook_loads_ten_questions(playbook):
    assert [q.id for q in playbook.questions][:2] == ["q1_book", "q2_growth_sources"]
    assert len(playbook.questions) == 10 and playbook.version


@pytest.mark.parametrize("mutate, msg", [
    (lambda d: d["questions"][0].update(kpi="profit"), "not an approved metric"),
    (lambda d: d["questions"][0].update(analyses=["change", "telepathy"]), "unknown analyses"),
    (lambda d: d["questions"][0].update(analyses=["change", "mix_rate"]), "ratio KPI"),
    (lambda d: d["segment_dimensions"].append("account_number"), "unknown dimension"),
])
def test_playbook_validation(model, tmp_path, mutate, msg):
    src = ROOT / "semantic" / "executive_playbook.yaml"
    data = yaml.safe_load(src.read_text(encoding="utf-8"))
    mutate(data)
    p = tmp_path / "pb.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(PlaybookError, match=msg):
        load_playbook(model, p)


# ---- templates ------------------------------------------------------------------------------------

def test_templates_are_governed_and_aggregate_only(model, playbook):
    t = Templates(model)
    c = comparison("mom", AS_OF)
    sqls = [t.segments(k, c, playbook.segment_dimensions) for k in
            ("total_outstanding", "gross_npa_ratio", "par_90_plus_ratio", "collection_efficiency", "disbursement_amount")]
    sqls += [t.flows(c.a.end, c.b.end), t.monthly_transitions(date(2025, 2, 28), c.a.end),
             t.concentration("par_90_plus_amount", c.a.end, c.b.end), t.trend("gross_npa_ratio", date(2024, 3, 31), AS_OF)]
    for s in sqls:
        assert "analytics_poc.v_account_summary" in s and ";" not in s
        assert not re.search(r"\braw_|v_dim_customers", s)
    # account ids are used only for joins/partitioning, never selected as output
    assert "acct AS" not in t.flows(c.a.end, c.b.end).split(" FROM a FULL")[0].split("SELECT CASE")[1]
    assert "account_number" not in t.concentration("npa_outstanding", c.a.end, c.b.end).split("SELECT count(*)")[1]


def test_segments_grouping_sets_use_column_references_only(model, playbook):
    s = Templates(model).segments("gross_npa_ratio", comparison("mom", AS_OF), playbook.segment_dimensions)
    sets = s.split("GROUPING SETS")[1]
    assert "CASE" not in sets and "(w)" in sets and '(w, "state_name")' in sets


def test_concentration_rejects_ratio_metric(model):
    with pytest.raises(UnsafeSQLError):
        Templates(model).concentration("gross_npa_ratio", date(2026, 2, 28), AS_OF)


def test_flow_kpi_uses_between_and_snapshot_uses_month_end(model):
    t = Templates(model)
    c = comparison("qoq", AS_OF)
    assert "BETWEEN DATE '2026-01-01' AND DATE '2026-03-31'" in t.segments("collection_efficiency", c, [])
    assert "\"transaction_date\" = DATE '2026-03-31'" in t.segments("gross_npa_ratio", c, [])


# ---- engine -----------------------------------------------------------------------------------------

def test_briefing_structure(briefing):
    assert isinstance(briefing, Briefing) and briefing.status == "DRAFT"
    assert len(briefing.questions) == 10 and len(briefing.tiles) == 4
    assert briefing.top_findings and briefing.questions[-1].id == "q10_summary"
    assert all(q.answer for q in briefing.questions)


def test_par90_question_bridge_and_findings(briefing):
    q5 = next(q for q in briefing.questions if q.id == "q5_par90")
    steps = {s.label: s.amount for s in q5.bridge.steps}
    assert steps["Rolled in from 61-90 DPD"] == 600 and steps["Cured to Current"] == -100
    assert abs(sum(steps.values()) - (q5.bridge.closing - q5.bridge.opening)) < 1e-9
    assert q5.comparisons["mom"].delta == pytest.approx(0.02)
    assert q5.concentration.accounts_for_50pct == 3
    kinds = {f.kind for f in q5.findings}
    assert {"change", "bridge", "contributor", "mix_rate"} <= kinds
    assert q5.rag in ("amber", "red", "green")
    assert q5.unusual.rank_of_13 == 1                      # planted jump in the last month


def test_segment_contributions_reconcile(briefing):
    q4 = next(q for q in briefing.questions if q.id == "q4_gnpa")
    for d, cs in q4.contributions.items():
        assert abs(sum(c.delta for c in cs) - q4.comparisons["mom"].delta) < 1e-12, d
    top = q4.contributions["state_name"][0]
    assert top.segment == "Seg-1" and top.share == pytest.approx(1.0)


def test_roll_rate_question_flags_worse_than_average(briefing):
    q6 = next(q for q in briefing.questions if q.id == "q6_roll_rates")
    r = next(x for x in q6.roll_rates if (x.from_bucket, x.to_bucket) == ("61-90 DPD", "90+ DPD"))
    assert r.rate == 0.6 and r.avg_rate_12m == pytest.approx(0.2)
    assert q6.findings and q6.rag in ("amber", "red")


def test_deterioration_and_concentration_questions(briefing):
    q7 = next(q for q in briefing.questions if q.id == "q7_deterioration")
    assert q7.findings and q7.extra["deterioration"][0]["segment"] == "Seg-1"
    q9 = next(q for q in briefing.questions if q.id == "q9_concentration")
    assert "Top 3 states hold" in q9.answer


def test_top_findings_ranked(briefing):
    scores = [f.score for f in briefing.top_findings]
    assert scores == sorted(scores, reverse=True) and len(scores) <= 5


def test_query_failure_marks_question_unavailable(model, playbook):
    b = BriefingEngine(model, playbook, FakeRunner(fail_on="FULL OUTER JOIN b ON a.acct = b.acct GROUP BY"),
                       workers=1).build(AS_OF)
    q6 = next(q for q in b.questions if q.id == "q6_roll_rates")
    assert "could not be computed" in q6.answer and q6.unavailable


def test_as_of_outside_range(model, playbook):
    with pytest.raises(ValueError):
        BriefingEngine(model, playbook, FakeRunner(), workers=1).build(date(2027, 1, 31))


def test_snapshot_has_no_account_identifiers(briefing):
    js = briefing.model_dump_json()
    assert "account_number" not in js and "acct" not in js and "customer_id" not in js


# ---- store --------------------------------------------------------------------------------------------

def test_store_roundtrip_and_approval_immutability(briefing, tmp_path):
    save(briefing, tmp_path)
    assert load(AS_OF, tmp_path).questions[0].answer == briefing.questions[0].answer
    assert list_briefings(tmp_path) == [(AS_OF, "DRAFT")]
    with pytest.raises(StoreError):
        approve(AS_OF, "  ", tmp_path)
    b = approve(AS_OF, "Jane (Finance)", tmp_path)
    assert b.status == "APPROVED" and b.approved_at
    with pytest.raises(StoreError, match="immutable"):
        save(briefing, tmp_path)
    with pytest.raises(StoreError, match="already approved"):
        approve(AS_OF, "Someone", tmp_path)
