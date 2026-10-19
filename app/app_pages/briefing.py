import os

import pandas as pd
import streamlit as st

from runtime import get_service, init_state, queue
from bi_investigate.store import StoreError, list_briefings, load
from bi_investigate.windows import shift_months
from bi_service.formatting import date_label, format_value, period_label

init_state()
model = get_service().model
SHOW_DRAFTS = os.environ.get("BI_SHOW_DRAFTS") == "1"
RAG_BADGE = {"green": ("On track", "green"), "amber": ("Watch", "orange"), "red": ("Act", "red")}

st.title("Executive briefing")
available = [(d, s) for d, s in list_briefings() if s == "APPROVED" or SHOW_DRAFTS]
if not available:
    st.info("No approved briefing yet. A reviewer approves each month-end briefing before it appears here.",
            title="Nothing to show")
    st.stop()

dates = [d for d, _ in available]
as_of = st.selectbox("Month-end", dates, format_func=lambda d: f"{date_label(d)}"
                     + ("" if dict(available)[d] == "APPROVED" else "  (DRAFT)"), key="briefing_as_of")
try:
    b = load(as_of)
except StoreError as e:
    st.error(str(e))
    st.stop()

if b.status == "APPROVED":
    st.caption(f":material/verified: Approved by **{b.approved_by}** on {b.approved_at[:10]} · "
               f"playbook {b.playbook_version} · {b.query_count} governed queries")
else:
    st.warning("Draft: not yet reviewed. Visible only because drafts are enabled.", title="Draft briefing")


def fmt(kpi: str, v) -> str:
    return format_value(v, model.metrics[kpi].format) if kpi in model.metrics else str(v)


def drill(spec: dict, label: str, key: str) -> None:
    s = dict(spec)
    dims = s.get("dimensions", [])
    has_time = any(model.dimensions[d].kind == "time" for d in dims)
    s.setdefault("period", {"kind": "range", "start": shift_months(as_of, -11).replace(day=1).isoformat(),
                            "end": as_of.isoformat()} if has_time else {"kind": "month", "month": f"{as_of:%Y-%m}"})
    if st.button(label, key=key, icon=":material/search:", on_click=queue,
                 args=({"spec": s, "display": f"Drill-down: {label}"},)):
        st.switch_page("app_pages/ask.py")


def drill_label(spec: dict) -> str:
    m = model.metrics[spec["metric"]].name
    dims = ", ".join(model.dimensions[d].label for d in spec.get("dimensions", []))
    return f"{m} by {dims}" if dims else m


# ---- KPI tiles ----------------------------------------------------------------------------------------
cols = st.columns(len(b.tiles))
for col, t in zip(cols, b.tiles):
    with col.container(border=True):
        pct = model.metrics[t.kpi].format.get("type") == "percent"
        worse_high = t.kpi != "collection_efficiency"
        st.metric(t.label, fmt(t.kpi, t.value), t.delta_label.split(" vs")[0],
                  delta_color="inverse" if (worse_high and pct) else "normal")
        if t.rag in RAG_BADGE:
            txt, color = RAG_BADGE[t.rag]
            st.badge(txt, color=color)

# ---- What leadership should look at --------------------------------------------------------------------
with st.container(border=True):
    st.subheader("What leadership should look at")
    for i, f in enumerate(b.top_findings, 1):
        st.markdown(f"**{i}.** {f.text}")
        if f.drilldown:
            drill(f.drilldown, drill_label(f.drilldown), f"top_{i}")


# ---- Questions -------------------------------------------------------------------------------------
def kpi_chart(q) -> None:
    if not (q.unusual and q.unusual.series):
        return
    pct = model.metrics[q.kpi].format.get("type") == "percent"
    cur = model.metrics[q.kpi].format.get("type") == "currency"
    y = model.metrics[q.kpi].name + (" (%)" if pct else " (₹ Cr)" if cur else "")
    df = pd.DataFrame([(period_label(d, "month"), None if v is None else v * (100 if pct else 1) / (1e7 if cur else 1))
                       for d, v in q.unusual.series], columns=["Month", y])
    df["Month"] = pd.Categorical(df["Month"], categories=list(df["Month"]), ordered=True)
    st.line_chart(df, x="Month", y=y, height=220, alt=f"{y} trend")


def analysis(q) -> None:
    tabs = []
    if q.bridge:
        tabs.append("Bridge")
    if q.contributions:
        tabs.append("Contributors")
    if q.mix_rate or q.num_den:
        tabs.append("Mix, rate & base")
    if q.roll_rates:
        tabs.append("Roll rates")
    if q.extra.get("deterioration"):
        tabs.append("Segments")
    if q.extra.get("shares"):
        tabs.append("Shares")
    if q.concentration:
        tabs.append("Concentration")
    if not tabs:
        return
    for name, tab in zip(tabs, st.tabs(tabs)):
        with tab:
            if name == "Bridge":
                rows = [("Opening", q.bridge.opening, None)] + [(s.label, s.amount, s.accounts) for s in q.bridge.steps] \
                       + [("Closing", q.bridge.closing, None)]
                df = pd.DataFrame([(l, a / 1e7, n) for l, a, n in rows], columns=["Step", "₹ Cr", "Accounts"])
                st.bar_chart(df, x="Step", y="₹ Cr", sort=False, height=240, alt="Bridge from opening to closing")
                st.dataframe(df, hide_index=True, alt="Bridge steps",
                             column_config={"₹ Cr": st.column_config.NumberColumn(format="%.2f")})
            elif name == "Contributors":
                dims = list(q.contributions)
                d = st.segmented_control("By", dims, default=dims[0], key=f"seg_{q.id}",
                                         format_func=lambda x: model.dimensions[x].label)
                ratio = model.metrics[q.kpi].measure.aggregation == "ratio"
                rows = [{"Segment": c.segment,
                         "Contribution": (c.delta * 100 if ratio else c.delta / 1e7),
                         "Share of change %": None if c.share is None else c.share * 100,
                         **({"Rate effect (pp)": (c.rate or 0) * 100, "Mix effect (pp)": (c.mix or 0) * 100} if ratio else {})}
                        for c in q.contributions[d or dims[0]]]
                st.dataframe(pd.DataFrame(rows), hide_index=True, alt="Contributors to the change",
                             column_config={"Contribution": st.column_config.NumberColumn(
                                 "Contribution (pp)" if ratio else "Contribution (₹ Cr)", format="%.3f")})
            elif name == "Mix, rate & base":
                if q.num_den:
                    st.markdown(f"Ratio change {q.num_den['total'] * 100:+.3f} pp = numerator effect "
                                f"{q.num_den['numerator'] * 100:+.3f} pp + base (denominator) effect "
                                f"{q.num_den['denominator'] * 100:+.3f} pp.")
                for dim, mr in (q.mix_rate or {}).items():
                    st.markdown(f"By {model.dimensions[dim].label.lower()}: rate {mr['rate'] * 100:+.3f} pp · mix {mr['mix'] * 100:+.3f} pp")
            elif name == "Roll rates":
                df = pd.DataFrame([{"From": r.from_bucket, "To": r.to_bucket, "Accounts": r.accounts,
                                    "Rate %": r.rate * 100, "12-mo avg %": None if r.avg_rate_12m is None else r.avg_rate_12m * 100}
                                   for r in q.roll_rates])
                st.dataframe(df, hide_index=True, alt="Bucket roll rates vs 12-month average",
                             column_config={"Rate %": st.column_config.NumberColumn(format="%.1f"),
                                            "12-mo avg %": st.column_config.NumberColumn(format="%.1f")})
            elif name == "Segments":
                df = pd.DataFrame(q.extra["deterioration"][:15])
                df["KPI"] = df["kpi"].map(lambda k: model.metrics[k].name)
                df["Before %"], df["After %"], df["Change pp"] = df["value_a"] * 100, df["value_b"] * 100, df["delta"] * 100
                st.dataframe(df[["comparison", "KPI", "dimension", "segment", "Before %", "After %", "Change pp"]],
                             hide_index=True, alt="Fastest deteriorating segments")
            elif name == "Shares":
                for dim, by_kpi in q.extra["shares"].items():
                    rows = [{"Segment": s, "Share of book %": by_kpi["total_outstanding"].get(s, 0) * 100,
                             "Share of NPA %": by_kpi["npa_outstanding"].get(s, 0) * 100}
                            for s in by_kpi["total_outstanding"]]
                    st.caption(model.dimensions[dim].label)
                    st.dataframe(pd.DataFrame(rows).sort_values("Share of NPA %", ascending=False), hide_index=True,
                                 alt=f"Book and NPA share by {dim}")
            elif name == "Concentration":
                c = q.concentration
                st.markdown(f"{c.accounts_changed} accounts moved; **{c.accounts_for_50pct}** explain 50% and "
                            f"**{c.accounts_for_80pct}** explain 80% of the gross movement "
                            f"(₹{c.gross_movement / 1e7:,.2f} Cr). Accounts are counted, never identified.")


themes: dict[str, list] = {}
for q in b.questions:
    if q.id != "q10_summary":
        themes.setdefault(q.theme, []).append(q)
for theme, qs in themes.items():
    st.header(theme)
    for q in qs:
        with st.container(border=True):
            head = st.container(horizontal=True, vertical_alignment="center")
            head.markdown(f"**{q.question}**")
            if q.rag in RAG_BADGE:
                txt, color = RAG_BADGE[q.rag]
                head.badge(txt, color=color)
            st.markdown(q.answer)
            if q.kpi and q.unusual:
                kpi_chart(q)
            with st.expander("Analysis", icon=":material/insights:"):
                analysis(q)
            if q.caveats:
                st.caption("What the data can't tell you: " + " ".join(q.caveats))
            if q.drilldowns:
                with st.container(horizontal=True):
                    for i, dd in enumerate(q.drilldowns):
                        drill(dd, drill_label(dd), f"dd_{q.id}_{i}")
            with st.expander("How this was calculated", icon=":material/calculate:"):
                for k, c in q.comparison_windows.items():
                    st.markdown(f"- **{k.upper()}**: {c.a.label} ({c.a.start} – {c.a.end}) → {c.b.label} ({c.b.start} – {c.b.end})")
                if q.kpi:
                    st.markdown(f"- KPI definition: {model.metrics[q.kpi].definition}")
                st.markdown("- All figures come from governed queries on approved KPI definitions; every decomposition "
                            "reconciles exactly to the total change.")
                for u in q.unavailable:
                    st.caption(f"Not available: {u}")
