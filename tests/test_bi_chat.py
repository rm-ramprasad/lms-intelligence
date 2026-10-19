"""Unit tests for bi_chat: catalog governance, period parser edges, renderer, audit, layering."""
import ast
import json
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from bi_chat.answer_renderer import render_answer
from bi_chat.audit import log_turn, redact
from bi_chat.catalog import CatalogError, load_catalog
from bi_chat.normalizer import tokenize
from bi_chat.period_parser import PeriodParser
from bi_service.query_models import ColumnMeta, QueryResult
from conftest import ROOT

L = date(2026, 3, 31)


@pytest.fixture(scope="module")
def catalog(model):
    return load_catalog(model)


# ---- catalog governance ------------------------------------------------------------------------

def test_every_approved_metric_has_glossary_entry(model, catalog):
    assert {m for m, x in model.metrics.items() if x.approved} <= set(catalog.kpis)
    for k in catalog.kpis.values():
        assert k.short and k.formula and k.synonyms and k.behavior_note and k.owner


@pytest.fixture
def semantic_copy(tmp_path):
    d = tmp_path / "semantic"
    shutil.copytree(ROOT / "semantic", d)
    return d


def _mutate(path, fn):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


@pytest.mark.parametrize("file, mutation, message", [
    ("glossary.yaml", lambda d: d["kpis"][0]["synonyms"].append("gnpa"), "maps to both"),
    ("glossary.yaml", lambda d: d["kpis"][0].update(metric_id="profit"), "not in metrics.yaml"),
    ("glossary.yaml", lambda d: d["kpis"][0].update(status="pending_approval"), "disagrees"),
    ("value_aliases.yaml", lambda d: d["dimensions"]["state_name"]["aliases"].update({"xyz": "Atlantis"}), "not a known value"),
    ("value_aliases.yaml", lambda d: d["dimensions"]["npa_flag"]["aliases"].update({"bad": "Yes"}), "true/false"),
    ("value_aliases.yaml", lambda d: d["dimensions"]["state_name"]["aliases"].update({"the": "Delhi"}), "stop word"),
])
def test_catalog_errors(model, semantic_copy, file, mutation, message):
    _mutate(semantic_copy / file, mutation)
    with pytest.raises(CatalogError, match=message):
        load_catalog(model, semantic_copy)


def test_uppercase_only_state_codes(model, catalog):
    from bi_chat.matcher import Matcher
    m = Matcher(catalog)
    assert m.parse("outstanding in UP", L).filters["state_name"]["values"] == ["Uttar Pradesh"]
    assert "state_name" not in m.parse("outstanding going up", L).filters


# ---- tokenizer & periods -----------------------------------------------------------------------

def test_tokenizer_keeps_business_tokens():
    assert tokenize("PAR 30+ % for FY25-26, 1-30 DPD & Mar-26 (1.5 Cr)") == \
        ["par", "30+", "%", "for", "fy25-26", "1-30", "dpd", "mar-26", "1.5", "cr"]


@pytest.mark.parametrize("text, period", [
    ("fy2025-26", {"kind": "fiscal_year", "year": 2026}),
    ("fiscal year 2025", {"kind": "fiscal_year", "year": 2025}),
    ("fy 2025-26", {"kind": "fiscal_year", "year": 2026}),
    ("2026-02", {"kind": "month", "month": "2026-02"}),
    ("april", {"kind": "month", "month": "2025-04"}),
    ("q4", {"kind": "range", "start": "2026-01-01", "end": "2026-03-31"}),
    ("q1 fy27", {"kind": "range", "start": "2026-04-01", "end": "2026-06-30"}),
    ("last 12 months", {"kind": "range", "start": "2025-04-01", "end": "2026-03-31"}),
    ("ytd", {"kind": "range", "start": "2025-04-01", "end": "2026-03-31"}),
    ("last month", {"kind": "latest"}),
])
def test_period_parser(catalog, text, period):
    assert PeriodParser(catalog.language).parse(tokenize(text), L).period == period


def test_may_alone_is_not_a_month(catalog):
    assert PeriodParser(catalog.language).parse(tokenize("may i see outstanding"), L) is None


def test_relative_periods_follow_anchor_not_today(catalog):
    p = PeriodParser(catalog.language).parse(tokenize("last quarter"), date(2025, 6, 30)).period
    assert p == {"kind": "range", "start": "2025-01-01", "end": "2025-03-31"}


# ---- answer renderer ---------------------------------------------------------------------------

def _result(columns, rows, total=None, period=None, snapshot="2026-03-31"):
    return QueryResult(status="success", columns=[ColumnMeta(name=n, type=t, role=r) for n, t, r in columns],
                       rows=rows, row_count=len(rows), total=total, snapshot_date=snapshot, period=period)


def test_render_ratio_by_category_with_total(model, catalog):
    r = _result([("state_name", "varchar", "dimension"), ("numerator", "decimal", "component"),
                 ("denominator", "decimal", "component"), ("value", "double", "measure")],
                [["Tamil Nadu", Decimal("168460657.91"), Decimal("2139935155.19"), 0.0787],
                 ["Delhi", Decimal("0"), Decimal("1035800000"), 0.0]],
                total=[Decimal("533368534.96"), Decimal("20340981809.89"), 0.0262])
    text = render_answer(r, {"metric": "gross_npa_ratio"}, model, catalog)
    assert "Gross NPA ratio" in text and "31 Mar 2026" in text
    assert "Tamil Nadu **7.87%**" in text and "Delhi **0.00%**" in text
    assert "Overall: **2.62%** (₹53.34 Cr of ₹2,034.10 Cr)" in text


def test_render_flow_trend(model, catalog):
    r = _result([("month", "date", "dimension"), ("value", "decimal", "measure")],
                [["2025-04-01", Decimal("50000000")], ["2026-03-01", Decimal("80000000")]],
                period={"kind": "fiscal_year", "start": "2025-04-01", "end": "2026-03-31"})
    text = render_answer(r, {"metric": "disbursement_amount"}, model, catalog)
    assert "in FY 2026" in text and "Apr 2025 **₹5.00 Cr**" in text and "Mar 2026 **₹8.00 Cr**" in text


def test_render_empty_and_failed(model, catalog):
    assert "No data" in render_answer(QueryResult(status="empty"), {"metric": "total_outstanding"}, model, catalog)
    assert "couldn't" in render_answer(QueryResult(status="timeout"), {"metric": "total_outstanding"}, model, catalog)


# ---- audit -------------------------------------------------------------------------------------

def test_audit_redacts_long_numbers_and_has_no_rows(tmp_path):
    p = tmp_path / "audit.jsonl"
    log_turn(p, session="s1", question="outstanding for account 1234567890", outcome="refuse")
    rec = json.loads(p.read_text().strip())
    assert "1234567890" not in rec["question"] and "##########" in rec["question"]
    assert "rows" not in rec
    assert redact("phone 9876543210") == "phone ##########"


# ---- layering ----------------------------------------------------------------------------------

def _imports(pkg: Path) -> set[str]:
    names = set()
    for f in pkg.rglob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        names |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    return names


AI_MODULES = {"llm_client.py", "llm_interpreter.py", "llm_narrator.py", "hybrid.py", "prompt_builder.py"}


def test_layering_and_llm_confinement():
    """bi_service never touches an LLM; in bi_chat only the Milestone 4 AI modules may; no 3rd-party LLM SDKs."""
    banned_llm = {"anthropic", "openai", "langchain"}
    assert "bi_chat" not in _imports(ROOT / "bi_service") and "app" not in _imports(ROOT / "bi_service")
    chat = _imports(ROOT / "bi_chat")
    assert "streamlit" not in chat and "app" not in chat and not chat & banned_llm
    for f in ROOT.joinpath("bi_service").rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        assert "bedrock-runtime" not in src and "converse(" not in src and "bi_chat" not in src, f
    for f in ROOT.joinpath("bi_chat").rglob("*.py"):
        if f.name not in AI_MODULES:
            assert "bedrock-runtime" not in f.read_text(encoding="utf-8") and "converse(" not in f.read_text(encoding="utf-8"), f
