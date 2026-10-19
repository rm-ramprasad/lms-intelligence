"""Headless UI smoke tests (streamlit AppTest) with a fake Athena client. No AWS, no LLM."""
import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from bi_service.athena_executor import AthenaExecutor
from bi_service.query_service import QueryService
from conftest import FakeAthena, FakeClock

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app" / "streamlit_app.py")
sys.path.insert(0, str(ROOT / "app"))
import runtime  # noqa: E402


class RoutingAthena(FakeAthena):
    """Latest-date lookups get a date; everything else gets a two-state result with a total row."""

    def start_query_execution(self, **kw):
        sql = kw["QueryString"]
        if sql.startswith('SELECT MAX("transaction_date") AS "snapshot_date" FROM'):
            self.columns, self.rows = [("snapshot_date", "date")], [["2026-03-31"]]
        else:
            self.columns = [("state_name", "varchar"), ("numerator", "decimal"), ("denominator", "decimal"),
                            ("value", "double"), ("snapshot_date", "date"), ("is_total", "integer")]
            self.rows = [[None, "30", "1000", "0.03", "2026-03-31", "1"],
                         ["Tamil Nadu", "20", "200", "0.1", "2026-03-31", "0"],
                         ["Delhi", "10", "800", "0.0125", "2026-03-31", "0"]]
        self._poll = 0
        return super().start_query_execution(**kw)


@pytest.fixture
def app(model, config, tmp_path, monkeypatch):
    fake = RoutingAthena()

    def factory():
        ex = AthenaExecutor(fake, workgroup=config.workgroup, database=config.database, timeout_seconds=5,
                            poll_interval_seconds=0, clock=FakeClock(), sleep=lambda s: None)
        return QueryService(model, ex, config)

    monkeypatch.setattr(runtime, "service_factory", factory)
    monkeypatch.setattr(runtime, "bedrock_factory", lambda: None)   # AI off unless a test enables it
    monkeypatch.setattr(runtime, "AUDIT_PATH", tmp_path / "audit.jsonl")
    for fn in (runtime.get_service, runtime.get_catalog, runtime.get_interpreter, runtime.latest_date,
               runtime._cached_run, runtime.get_bedrock, runtime.get_router, runtime._ai_store):
        fn.clear()
    at = AppTest.from_file(APP, default_timeout=20).run()
    assert not at.exception
    at.switch_page("app_pages/ask.py").run()
    return at, fake, tmp_path


def test_ask_page_answers_question(app):
    at, fake, tmp = app
    at.chat_input[0].set_value("gnpa by state").run()
    assert not at.exception
    text = " ".join(m.value for m in at.markdown)
    assert "Gross NPA ratio" in text and "Tamil Nadu **10.00%**" in text and "Overall: **3.00%**" in text
    assert len(at.dataframe) >= 1
    assert (tmp / "audit.jsonl").exists()


def test_ask_page_clarifies_and_option_runs(app):
    at, fake, _ = app
    at.chat_input[0].set_value("npa by state").run()
    labels = [b.label for b in at.button]
    assert "Gross NPA ratio" in labels
    next(b for b in at.button if b.label == "Gross NPA ratio").click().run()
    assert not at.exception
    assert any("Gross NPA ratio" in m.value and "Overall" in m.value for m in at.markdown)


def test_ask_page_refuses_account_lookup(app):
    at, fake, tmp = app
    n_before = len([c for c in fake.calls if c[0] == "start"])
    at.chat_input[0].set_value("outstanding for account 1234567890").run()
    assert any("portfolio-level" in m.value for m in at.markdown)
    assert len([c for c in fake.calls if c[0] == "start"]) == n_before     # nothing sent to Athena
    assert "1234567890" not in (tmp / "audit.jsonl").read_text()


def test_repeated_question_served_from_cache(app):
    import json
    at, fake, tmp = app
    starts = lambda: len([c for c in fake.calls if c[0] == "start"])  # noqa: E731
    at.chat_input[0].set_value("gnpa by state").run()
    after_first = starts()
    at.chat_input[0].set_value("gnpa by state").run()
    assert not at.exception and starts() == after_first           # second answer did not hit Athena
    recs = [json.loads(line) for line in (tmp / "audit.jsonl").read_text().splitlines()]
    assert [r["cached"] for r in recs if r["outcome"] == "interpreted"] == [False, True]


def test_failures_are_not_cached(model, config, tmp_path, monkeypatch):
    fake = FakeAthena(states=["FAILED"])
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        ex = AthenaExecutor(fake, workgroup=config.workgroup, database=config.database, timeout_seconds=5,
                            poll_interval_seconds=0, clock=FakeClock(), sleep=lambda s: None)
        return QueryService(model, ex, config)

    monkeypatch.setattr(runtime, "service_factory", factory)
    runtime.get_service.clear()
    runtime._cached_run.clear()
    spec = json_key = '{"metric": "total_outstanding"}'
    with pytest.raises(runtime._NotCacheable):
        runtime._cached_run(json_key)
    with pytest.raises(runtime._NotCacheable):   # retried, not served from cache
        runtime._cached_run(spec)
    assert len([c for c in fake.calls if c[0] == "start"]) == 2


def test_hybrid_ai_path_shows_badge_and_never_sends_pii(model, config, tmp_path, monkeypatch):
    from bi_chat.llm_client import BedrockClient
    from test_bedrock import SETTINGS, FakeRuntime, text, tool

    fake = RoutingAthena()
    rt = FakeRuntime(tool("run_query", {"metric": "gross_npa_ratio", "dimensions": ["state_name"]}),
                     text("Gross NPA ratio was 3.00% overall; Tamil Nadu is highest at 10.00%."))

    def factory():
        ex = AthenaExecutor(fake, workgroup=config.workgroup, database=config.database, timeout_seconds=5,
                            poll_interval_seconds=0, clock=FakeClock(), sleep=lambda s: None)
        return QueryService(model, ex, config)

    monkeypatch.setattr(runtime, "service_factory", factory)
    monkeypatch.setattr(runtime, "bedrock_factory", lambda: BedrockClient(rt, SETTINGS))
    monkeypatch.setattr(runtime, "AUDIT_PATH", tmp_path / "audit.jsonl")
    for fn in (runtime.get_service, runtime.get_catalog, runtime.get_interpreter, runtime.latest_date,
               runtime._cached_run, runtime.get_bedrock, runtime.get_router, runtime._ai_store):
        fn.clear()
    at = AppTest.from_file(APP, default_timeout=20).run()
    at.switch_page("app_pages/ask.py").run()
    at.session_state["ai_summary"] = True
    at.chat_input[0].set_value("how bad are the loans per state lol").run()
    assert not at.exception
    text_all = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Understood by AI" in text_all and "Understood as: Gross NPA ratio" in text_all
    assert "Tamil Nadu is highest at 10.00%" in text_all          # faithful summary shown
    n_calls = len(rt.requests)
    at.chat_input[0].set_value("npa for account 1234567890").run()
    assert len(rt.requests) == n_calls                              # PII question never reached Bedrock
    rec = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert any(r.get("source") == "ai" and r.get("ai", {}).get("prompt_hash") for r in rec)


def test_catalog_and_builder_pages_render(app):
    at, _, _ = app
    at.switch_page("app_pages/catalog.py").run()
    assert not at.exception and any("Gross NPA ratio" in m.value for m in at.markdown)
    at.switch_page("app_pages/builder.py").run()
    assert not at.exception
    at.selectbox[0].select("gross_npa_ratio")
    at.multiselect[0].select("state_name")
    at.button[0].click().run()   # form submit
    assert not at.exception and any("Overall" in m.value for m in at.markdown)
