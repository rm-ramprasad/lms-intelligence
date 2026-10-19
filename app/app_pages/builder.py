import uuid
from datetime import date

import streamlit as st

from runtime import get_catalog, get_service, init_state, latest_date, render_answer, render_result, run_spec

init_state()
st.title("Guided builder")
st.caption("Pick a KPI, breakdown, filters and period. Produces the same governed query as a typed question.")

svc, cat = get_service(), get_catalog()
model = svc.model
approved = [m for m in model.metrics.values() if m.approved]
anchor = latest_date() or date.today()

with st.form("builder"):
    metric_id = st.selectbox("KPI", [m.id for m in approved], format_func=lambda m: cat.kpis[m].name)
    dims = st.multiselect("Break down by", list(model.dimensions), max_selections=2,
                          format_func=lambda d: model.dimensions[d].label)
    fcol, vcol = st.columns([1, 2], vertical_alignment="bottom")
    filterable = [d for d in model.dimensions.values() if d.filterable]
    fdim = fcol.selectbox("Filter on", ["(none)"] + [d.id for d in filterable],
                          format_func=lambda d: d if d == "(none)" else model.dimensions[d].label)
    all_values = sorted({str(v) for d in filterable for v in cat.values.get(d.id, [])}) + ["Yes", "No"]
    fvals = vcol.multiselect("Values", all_values, help="Values must belong to the chosen filter")
    kind = st.segmented_control("Period", ["Latest", "Month", "Fiscal year", "Range"], default="Latest")
    pcol1, pcol2 = st.columns(2)
    month = pcol1.date_input("Month (any day in it)", value=anchor)
    fy = pcol1.number_input("Fiscal year (end year)", 2000, 2100, value=anchor.year + (1 if anchor.month >= 4 else 0))
    start = pcol2.date_input("Range start", value=date(anchor.year, 1, 1))
    end = pcol2.date_input("Range end", value=anchor)
    top_n = st.number_input("Top N (0 = all)", 0, 100, value=0)
    submitted = st.form_submit_button("Run", icon=":material/play_arrow:", type="primary")

if submitted:
    spec = {"metric": metric_id, "dimensions": dims, "filters": []}
    if fdim != "(none)" and fvals:
        dim = model.dimensions[fdim]
        if dim.type == "boolean":
            spec["filters"] = [{"dimension": fdim, "op": "eq", "value": fvals[0] == "Yes"}]
        else:
            spec["filters"] = [{"dimension": fdim, "op": "in" if len(fvals) > 1 else "eq",
                                "value": fvals if len(fvals) > 1 else fvals[0]}]
    spec["period"] = {
        "Latest": {"kind": "latest"},
        "Month": {"kind": "month", "month": f"{month:%Y-%m}"},
        "Fiscal year": {"kind": "fiscal_year", "year": int(fy)},
        "Range": {"kind": "range", "start": start.isoformat(), "end": end.isoformat()},
    }[kind or "Latest"]
    if top_n:
        spec.update(sort=[{"by": "value", "direction": "desc"}], limit=int(top_n))
    metric = model.metrics[metric_id]
    has_time = any(model.dimensions[d].kind == "time" for d in dims)
    spec["include_total"] = bool(dims) and not (metric.behavior == "snapshot" and has_time)

    result = run_spec(spec, f"[builder] {spec}", "builder")
    if result.status == "rejected":
        st.error("; ".join(e.message for e in result.errors), title="This combination isn't allowed")
    else:
        msg = {"id": uuid.uuid4().hex[:8], "spec": spec, "result": result.model_dump(),
               "text": render_answer(result, spec, model, cat)}
        render_result(msg, interactive=True)
