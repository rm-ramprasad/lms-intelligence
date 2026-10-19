"""Shared app plumbing: cached service objects, turn handling, and result rendering.

Pages stay thin scripts; all logic that does not touch Streamlit lives in bi_chat / bi_service.
"""
import json
import os
import sys
import threading
import time
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bi_chat.answer_renderer import dimension_label, render_answer  # noqa: E402
from bi_chat.audit import log_turn  # noqa: E402
from bi_chat.catalog import load_catalog  # noqa: E402
from bi_chat.hybrid import HybridInterpreter  # noqa: E402
from bi_chat.interpreter import Clarify, ConversationState, DeterministicInterpreter, Interpreted, Refuse  # noqa: E402
from bi_chat.llm_client import BedrockClient, BedrockSettings  # noqa: E402
from bi_chat.llm_interpreter import LlmInterpreter  # noqa: E402
from bi_chat.llm_narrator import narrate  # noqa: E402
from bi_service.config import ServiceConfig  # noqa: E402
from bi_service.formatting import date_label, format_value  # noqa: E402
from bi_service.query_models import QueryResult  # noqa: E402
from bi_service.query_service import QueryService  # noqa: E402
from bi_service.semantic_loader import load_semantic_model  # noqa: E402

AUDIT_PATH = ROOT / "logs" / "audit.jsonl"
FEEDBACK_PATH = ROOT / "logs" / "feedback.jsonl"
ADMIN = os.environ.get("BI_ADMIN") == "1"
CACHE_TTL_SECONDS = int(os.environ.get("BI_CACHE_TTL_SECONDS", 900))   # answer cache; data changes monthly

# Test seam: tests replace this with a factory returning a QueryService over a fake Athena client.
service_factory = QueryService.from_defaults


@st.cache_resource
def get_service() -> QueryService:
    return service_factory()


@st.cache_resource
def get_catalog():
    return load_catalog(get_service().model)


@st.cache_resource
def get_interpreter() -> DeterministicInterpreter:
    svc = get_service()
    return DeterministicInterpreter(svc.model, get_catalog(), svc.compile)


def _default_bedrock_factory():
    s = BedrockSettings.load()
    if not s.enabled:
        return None
    cfg = ServiceConfig.load()
    return BedrockClient.from_aws(cfg.profile, cfg.region, s)


# Test seam: tests replace this with a factory returning a BedrockClient over a fake runtime.
bedrock_factory = _default_bedrock_factory


@st.cache_resource
def get_bedrock() -> BedrockClient | None:
    try:
        return bedrock_factory()
    except Exception:   # no credentials / disabled: the app stays fully usable in catalog-rules mode
        return None


@st.cache_resource
def get_router() -> HybridInterpreter:
    client = get_bedrock()
    det = get_interpreter()
    return HybridInterpreter(det, LlmInterpreter(det, client) if client else None)


@st.cache_resource
def _ai_store() -> dict:
    """Shared TTL store for AI interpretations and summaries (only successful AI outputs are stored)."""
    return {}


def _ai_get(key: str):
    hit = _ai_store().get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < CACHE_TTL_SECONDS else None


def _ai_put(key: str, value) -> None:
    store = _ai_store()
    if len(store) > 1000:
        store.clear()
    store[key] = (time.monotonic(), value)


@st.cache_data(ttl=3600, show_spinner=False)
def latest_date() -> date | None:
    return get_service().latest_snapshot_date()


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("session_id", uuid.uuid4().hex[:12])
    ss.setdefault("messages", [])
    ss.setdefault("conversation", ConversationState())
    ss.setdefault("pending", None)        # action queued by a button or another page
    ss.setdefault("show_sql", False)
    ss.setdefault("mode", "Hybrid")       # E3: Hybrid is the demo default
    ss.setdefault("ai_summary", False)    # E2: off by default


MODE_KEYS = {"Catalog rules": "deterministic", "AI": "ai", "Hybrid": "hybrid"}


def ai_available() -> bool:
    return get_router().llm is not None


# ---- turn handling -----------------------------------------------------------------------------

class _NotCacheable(Exception):
    """Raised inside the cached function so failures/timeouts are never cached (exceptions aren't)."""

    def __init__(self, result: dict):
        self.result = result


@st.cache_data(ttl=CACHE_TTL_SECONDS, max_entries=500, show_spinner=False)
def _cached_run(spec_key: str) -> dict:
    _local.calls = getattr(_local, "calls", 0) + 1   # only incremented on a cache miss (body actually runs)
    result = get_service().run(json.loads(spec_key))
    if result.status not in ("success", "empty"):
        raise _NotCacheable(result.model_dump())
    return result.model_dump()


def run_spec(spec: dict, question: str, outcome: str, **extra) -> QueryResult:
    """Execute via a shared answer cache: the same governed spec within the TTL is served without Athena.
    Data changes monthly, so a short TTL is safe. Rejections never reach Athena and aren't cached."""
    key = json.dumps(spec, sort_keys=True, default=str)
    before = _cached_run_calls()
    try:
        result = QueryResult.model_validate(_cached_run(key))
    except _NotCacheable as e:
        result = QueryResult.model_validate(e.result)
    cached = _cached_run_calls() == before
    log_turn(AUDIT_PATH, session=st.session_state.session_id, question=question, outcome=outcome, spec=spec,
             status=result.status, query_id=result.query_id, ms=0 if cached else result.execution_time_ms,
             bytes_scanned=0 if cached else result.bytes_scanned, cached=cached, **extra)
    st.session_state["last_cached"] = cached
    return result


_local = threading.local()   # per-thread miss counter; Streamlit runs each session's script in its own thread


def _cached_run_calls() -> int:
    return getattr(_local, "calls", 0)


def handle(question: str, *, example: str | None = None, **force) -> dict:
    """Interpret and (if possible) execute one turn. Returns the assistant message dict."""
    anchor = latest_date()
    msg_id = uuid.uuid4().hex[:8]
    if anchor is None:
        return {"id": msg_id, "role": "assistant", "kind": "error",
                "text": "I can't reach the data right now (latest snapshot date unavailable). Please try again."}
    mode = MODE_KEYS.get(st.session_state.get("mode", "Hybrid"), "hybrid")
    if example:
        ex = get_service().model.examples[example]
        r = Interpreted(dict(ex.spec), [("example", ex.question)])
        question = ex.question
    else:
        prev = st.session_state.conversation.last_spec
        key = json.dumps([question, prev, anchor.isoformat(), mode, force], sort_keys=True, default=str)
        r = _ai_get(key)
        if r is None:
            r = get_router().interpret(question, st.session_state.conversation, anchor=anchor, mode=mode, **force)
            if r.source == "ai":
                _ai_put(key, r)
    extra = {"source": r.source, "mode": mode, **({"ai": r.meta} if r.source == "ai" else {}),
             **({"ai_fallback": r.meta["ai_fallback"]} if "ai_fallback" in r.meta else {})}

    if isinstance(r, Interpreted):
        result = run_spec(r.spec, question, "example" if example else "interpreted", **extra)
        if result.status == "success":
            st.session_state.conversation = ConversationState(r.spec)
        text = render_answer(result, r.spec, get_service().model, get_catalog())
        msg = {"id": msg_id, "role": "assistant", "kind": "answer", "text": text, "spec": r.spec,
               "result": result.model_dump(), "notes": r.notes, "cached": st.session_state.get("last_cached", False),
               "source": r.source, "ai_meta": r.meta, "question": question}
        if st.session_state.get("ai_summary") and result.status == "success":
            msg["summary"], msg["summary_meta"] = summarise(result, r.spec, question)
        return msg
    kind = "clarify" if isinstance(r, Clarify) else "refuse"
    unrecognised = next((o.action["ignore"] for o in r.options if "ignore" in o.action), [])
    log_turn(AUDIT_PATH, session=st.session_state.session_id, question=question, outcome=kind,
             unrecognised=unrecognised, reason=r.reason, **extra)
    return {"id": msg_id, "role": "assistant", "kind": kind, "text": r.message, "source": r.source,
            "ai_meta": r.meta, "options": [{"label": o.label, "action": o.action} for o in r.options]}


def summarise(result: QueryResult, spec: dict, question: str) -> tuple[str | None, dict]:
    client = get_bedrock()
    if client is None:
        return None, {"reason": "ai_unavailable"}
    key = "summary:" + json.dumps([spec, result.query_id, result.snapshot_date], sort_keys=True, default=str)
    hit = _ai_get(key)
    if hit:
        return hit
    text, meta = narrate(client, result, spec, get_service().model, get_catalog())
    log_turn(AUDIT_PATH, session=st.session_state.session_id, question=question, outcome="summary",
             spec=spec, summary_status=meta.get("reason"), ai={k: v for k, v in meta.items() if k != "reason"})
    if text:
        _ai_put(key, (text, meta))
    return text, meta


def spec_summary(spec: dict) -> str:
    """Human-readable 'understood as' line for the interpreted spec."""
    model, cat = get_service().model, get_catalog()
    parts = [cat.kpis[spec["metric"]].name]
    if spec.get("dimensions"):
        parts.append("by " + ", ".join(model.dimensions[d].label for d in spec["dimensions"]))
    for f in spec.get("filters", []):
        v = f["value"]
        v = ", ".join(map(str, v)) if isinstance(v, list) else ("Yes" if v is True else "No" if v is False else v)
        parts.append(f"{model.dimensions[f['dimension']].label} {'≠' if f['op'] in ('ne', 'not_in') else '='} {v}")
    p = spec.get("period") or {"kind": "latest"}
    parts.append({"latest": "latest", "month": p.get("month"), "fiscal_year": f"FY {p.get('year')}",
                  "calendar_year": str(p.get("year")), "range": f"{p.get('start')} to {p.get('end')}"}.get(p["kind"], ""))
    if spec.get("limit") and spec.get("sort"):
        parts.append(f"{'top' if spec['sort'][0]['direction'] == 'desc' else 'bottom'} {spec['limit']}")
    return " · ".join(x for x in parts if x)


def render_source_badge(msg: dict) -> None:
    if msg.get("source") == "ai":
        meta = msg.get("ai_meta", {})
        st.badge(f"Understood by AI · {meta.get('model', 'Bedrock')}", icon=":material/auto_awesome:", color="violet")
    elif msg.get("kind") == "answer":
        st.badge("Understood by catalog rules", icon=":material/rule:", color="gray")


def queue(action: dict) -> None:
    st.session_state.pending = action


def process_pending() -> None:
    action = st.session_state.pending
    if not action:
        return
    st.session_state.pending = None
    question = action.get("question") or ""
    if action.get("example"):
        question = get_service().model.examples[action["example"]].question
    shown = question if (action.get("example") or "display" not in action) else action["display"]
    st.session_state.messages.append({"id": uuid.uuid4().hex[:8], "role": "user", "text": shown})
    kw = {k: action[k] for k in ("force_metric", "force_period", "ignore", "example") if k in action}
    st.session_state.messages.append(handle(question, **kw))


# ---- rendering ---------------------------------------------------------------------------------

def result_frame(result: QueryResult, spec: dict, display: bool) -> pd.DataFrame:
    model = get_service().model
    metric = model.metrics[spec["metric"]]
    vfmt = metric.format
    cfmt = model.metrics[metric.measure.numerator].format if metric.measure.aggregation == "ratio" else None
    cols, data = [], []
    labels = {"numerator": "Numerator", "denominator": "Denominator", "value": get_catalog().kpis[metric.id].name}
    for c in result.columns:
        if c.role == "dimension":
            cols.append(model.dimensions[c.name].label)
        else:
            cols.append(labels.get(c.name, c.name))
    for r in result.rows:
        row = []
        for c, v in zip(result.columns, r):
            if c.role == "dimension":
                row.append(dimension_label(model, c.name, v) if display else v)
            elif display:
                row.append(format_value(v, vfmt if c.role == "measure" else cfmt))
            else:
                row.append(float(v) if v is not None else None)
        data.append(row)
    return pd.DataFrame(data, columns=cols)


def render_chart(result: QueryResult, spec: dict) -> None:
    model = get_service().model
    dims = [c.name for c in result.columns if c.role == "dimension"]
    if len(dims) != 1 or not result.rows:
        return
    metric = model.metrics[spec["metric"]]
    pct = metric.format.get("type") == "percent"
    cr = metric.format.get("type") == "currency"
    unit = " (%)" if pct else " (₹ Cr)" if cr else ""
    name = get_catalog().kpis[metric.id].name + unit
    dim = model.dimensions[dims[0]]
    vals = []
    for r in result.rows:
        v = r[-1]
        v = None if v is None else float(v) * (100 if pct else 1) / (1e7 if cr else 1)
        vals.append((dimension_label(model, dims[0], r[0]), v))
    df = pd.DataFrame(vals, columns=[dim.label, name])
    if dim.kind == "time":
        df[dim.label] = pd.Categorical(df[dim.label], categories=list(df[dim.label]), ordered=True)
        st.line_chart(df, x=dim.label, y=name, height=260, alt=f"{name} by {dim.label}")
    else:
        st.bar_chart(df, x=dim.label, y=name, height=260, sort=False, alt=f"{name} by {dim.label}")


def render_details(result: QueryResult, spec: dict, cached: bool = False) -> None:
    model, cat = get_service().model, get_catalog()
    kpi = cat.kpis[spec["metric"]]
    with st.expander("How this was calculated", icon=":material/calculate:"):
        st.markdown(f"**{kpi.name}**: {kpi.long}")
        st.markdown(f"Formula: `{kpi.formula}`  \n{kpi.behavior_note}")
        p = result.period or {}
        when = "latest snapshot" if p.get("kind") == "latest" else f"{p.get('start')} to {p.get('end')}"
        st.markdown(f"Period: {when}  \nSnapshot used: {date_label(result.snapshot_date)}")
        filters = spec.get("filters") or []
        if filters:
            st.markdown("Filters: " + "; ".join(
                f"{model.dimensions[f['dimension']].label} {f['op'].replace('_', ' ')} {f['value']}" for f in filters))
        st.caption(f"Query id {result.query_id} · {result.execution_time_ms} ms · "
                   f"{(result.bytes_scanned or 0) / 1e6:.1f} MB scanned"
                   + (f" · served from cache (refreshes every {CACHE_TTL_SECONDS // 60} min)" if cached else ""))
        if ADMIN and st.session_state.get("show_sql") and result.sql:
            st.code(result.sql, language="sql")


def render_result(msg: dict, interactive: bool) -> None:
    result = QueryResult.model_validate(msg["result"])
    spec = msg["spec"]
    render_source_badge(msg)
    if msg.get("source") == "ai":
        st.caption(f"Understood as: {spec_summary(spec)}. Not right? Rephrase, or use the Guided builder.")
    st.markdown(msg["text"])
    if msg.get("summary"):
        with st.container(border=True):
            st.badge("AI summary", icon=":material/auto_awesome:", color="violet")
            st.markdown(msg["summary"])
    elif msg.get("summary_meta"):
        st.caption(f"AI summary not shown ({msg['summary_meta'].get('reason')}); the governed answer above is unaffected.")
    for note in msg.get("notes", []):
        st.caption(note)
    if result.status != "success":
        return
    render_chart(result, spec)
    df = result_frame(result, spec, display=True)
    st.dataframe(df, hide_index=True, alt="Result table")
    with st.container(horizontal=True, vertical_alignment="center"):
        st.download_button("CSV", result_frame(result, spec, display=False).to_csv(index=False).encode(),
                           file_name=f"{spec['metric']}.csv", mime="text/csv", icon=":material/download:",
                           key=f"dl_{msg['id']}")
        fb = st.feedback("thumbs", key=f"fb_{msg['id']}")
        if fb is not None and not st.session_state.get(f"fb_logged_{msg['id']}"):
            st.session_state[f"fb_logged_{msg['id']}"] = True
            log_turn(FEEDBACK_PATH, session=st.session_state.session_id, question=msg.get("question", ""),
                     outcome="feedback_up" if fb == 1 else "feedback_down", spec=spec, query_id=result.query_id)
    render_details(result, spec, msg.get("cached", False))


def render_options(msg: dict, interactive: bool) -> None:
    render_source_badge(msg)
    st.markdown(msg["text"])
    if not msg.get("options"):
        return
    with st.container(horizontal=True):
        for i, o in enumerate(msg["options"]):
            st.button(o["label"], key=f"opt_{msg['id']}_{i}", disabled=not interactive,
                      on_click=queue, args=({**o["action"], "display": o["label"]},))
