"""Entry point: streamlit run app/streamlit_app.py"""
import streamlit as st

from runtime import ADMIN, MODE_KEYS, ai_available, get_bedrock, init_state, latest_date

st.set_page_config(page_title="Portfolio Q&A", page_icon=":material/query_stats:", layout="wide")
init_state()

pages = [
    st.Page("app_pages/briefing.py", title="Executive briefing", icon=":material/dashboard:", default=True),
    st.Page("app_pages/ask.py", title="Ask", icon=":material/chat:"),
    st.Page("app_pages/catalog.py", title="KPI catalog", icon=":material/menu_book:"),
    st.Page("app_pages/builder.py", title="Guided builder", icon=":material/tune:"),
]
nav = st.navigation(pages)

with st.sidebar:
    d = latest_date()
    st.caption(f"Data as of **{d:%d %b %Y}**" if d else "Data date unavailable")
    st.caption("Answers are computed from approved KPI definitions only. Mock data.")
    st.segmented_control("Question understanding", list(MODE_KEYS), key="mode",
                         disabled=not ai_available(),
                         help="Catalog rules: deterministic. AI: Amazon Bedrock. Hybrid: catalog rules first, AI when "
                              "they can't understand the question.")
    st.toggle("AI summary", key="ai_summary", disabled=not ai_available(),
              help="Adds a short AI-written summary of the aggregated result. Numbers are checked against the result.")
    client = get_bedrock()
    if client is None:
        st.caption(":material/info: AI unavailable; using catalog rules only.")
    else:
        st.caption(f"AI: {client.s.model_label} · guardrail {'on' if client.use_guardrail else 'off'}")
    if ADMIN:
        st.toggle("Show generated SQL", key="show_sql")

nav.run()
