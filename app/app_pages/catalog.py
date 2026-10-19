import pandas as pd
import streamlit as st

from runtime import get_catalog, get_service, init_state, queue

init_state()
st.title("KPI catalog")
st.caption("Approved definitions and the words the assistant understands. Source: semantic/glossary.yaml.")

cat, model = get_catalog(), get_service().model
query = st.text_input("Search KPIs, synonyms or terms", placeholder="e.g. NPA, AUM, collection").strip().lower()


def matches(*texts) -> bool:
    return not query or any(query in t.lower() for t in texts if t)


UNITS = {"currency": "₹ Cr", "percent": "%", "integer": "count"}
examples_by_metric: dict[str, list] = {}
for ex in model.examples.values():
    examples_by_metric.setdefault(ex.spec["metric"], []).append(ex)

st.subheader("KPIs")
for k in cat.kpis.values():
    if not matches(k.name, k.short, k.long, *k.synonyms):
        continue
    metric = model.metrics[k.metric_id]
    with st.container(border=True):
        st.markdown(f"**{k.name}**" + ("" if k.status == "approved" else "  :orange-badge[Pending approval]"))
        st.markdown(k.long)
        st.markdown(f"Formula: `{k.formula}` · Unit: {UNITS.get(metric.format.get('type'), '-')} · "
                    f"{'Month-end balance' if metric.behavior == 'snapshot' else 'Flow over a period'} · Owner: {k.owner}")
        st.caption(k.behavior_note)
        st.markdown("Also understood as: " + ", ".join(f"`{s}`" for s in k.synonyms))
        for ex in examples_by_metric.get(k.metric_id, []):
            if st.button(ex.question, key=f"cat_ex_{ex.id}", icon=":material/play_arrow:",
                         on_click=queue, args=({"example": ex.id},)):
                st.switch_page("app_pages/ask.py")

st.subheader("Breakdowns and filters")
rows = []
for d in model.dimensions.values():
    syn = cat.dimension_synonyms.get(d.id, ())
    vals = cat.values.get(d.id, ["Yes", "No"] if d.type == "boolean" else [])
    if d.kind == "time":
        vals = ["Month", "Quarter (fiscal)", "Year"]
    if matches(d.label, *syn, *map(str, vals)):
        rows.append({"Breakdown": d.label, "Say": ", ".join(syn), "Values": ", ".join(map(str, vals))})
st.dataframe(pd.DataFrame(rows), hide_index=True, alt="Dimensions and values")

st.subheader("Terms")
for t in cat.terms:
    if matches(t["term"], t["definition"]):
        st.markdown(f"**{t['term']}**: {t['definition']}")
