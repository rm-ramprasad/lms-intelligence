"""Executive Briefing page (AppTest) from a fixture snapshot. No AWS."""
import sys
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import bi_investigate.store as store
from bi_investigate.engine import BriefingEngine
from bi_investigate.playbook import load_playbook
from bi_service.athena_executor import AthenaExecutor
from bi_service.query_service import QueryService
from conftest import FakeClock
from test_app_smoke import RoutingAthena
from test_briefing_engine import AS_OF, FakeRunner

ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app" / "streamlit_app.py")
sys.path.insert(0, str(ROOT / "app"))
import runtime  # noqa: E402


@pytest.fixture
def app_with_briefing(model, config, tmp_path, monkeypatch):
    b = BriefingEngine(model, load_playbook(model), FakeRunner(), workers=1).build(AS_OF)
    monkeypatch.setattr(store, "ROOT", tmp_path / "briefings")
    store.save(b)
    fake = RoutingAthena()

    def factory():
        ex = AthenaExecutor(fake, workgroup=config.workgroup, database=config.database, timeout_seconds=5,
                            poll_interval_seconds=0, clock=FakeClock(), sleep=lambda s: None)
        return QueryService(model, ex, config)

    monkeypatch.setattr(runtime, "service_factory", factory)
    monkeypatch.setattr(runtime, "bedrock_factory", lambda: None)
    monkeypatch.setattr(runtime, "AUDIT_PATH", tmp_path / "audit.jsonl")
    for fn in (runtime.get_service, runtime.get_catalog, runtime.get_interpreter, runtime.latest_date,
               runtime._cached_run, runtime.get_bedrock, runtime.get_router, runtime._ai_store):
        fn.clear()
    return b, fake, monkeypatch


def text_of(at) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption] +
                    [str(x.value) for x in at.info] + [str(x.value) for x in at.warning])


def test_drafts_hidden_from_executives(app_with_briefing):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and "No approved briefing yet" in text_of(at)


def test_approved_briefing_renders(app_with_briefing):
    store.approve(AS_OF, "Jane (Finance)")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    t = text_of(at)
    assert "Approved by" in t and "Jane (Finance)" in t
    assert "What leadership should look at" in [h.value for h in at.subheader]
    assert {"Growth", "Asset quality", "Early warning", "Collections", "Concentration"} <= {h.value for h in at.header}
    assert len(at.metric) == 4
    assert "What drove the change in PAR 90+?" in t


def test_drafts_visible_when_enabled(app_with_briefing):
    _, _, mp = app_with_briefing
    mp.setenv("BI_SHOW_DRAFTS", "1")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and "Draft: not yet reviewed" in text_of(at)


def test_drilldown_opens_ask_with_governed_spec(app_with_briefing):
    b, fake, _ = app_with_briefing
    store.approve(AS_OF, "Jane (Finance)")
    at = AppTest.from_file(APP, default_timeout=30).run()
    btn = next(x for x in at.button if x.key and x.key.startswith("dd_q4_gnpa"))
    btn.click().run()
    at.switch_page("app_pages/ask.py").run()
    assert not at.exception
    t = text_of(at)
    assert "Drill-down:" in t and "Gross NPA ratio" in t
    assert any(c[0] == "start" for c in fake.calls)   # the drill-down ran as a normal governed query
