import streamlit as st

from runtime import get_service, handle, init_state, process_pending, queue, render_options, render_result

init_state()
st.title("Ask about the portfolio")

prompt = st.chat_input("Ask about outstanding, NPA, PAR, disbursement, collections…", submit_mode="disable")
if prompt:
    st.session_state.messages.append({"id": f"u{len(st.session_state.messages)}", "role": "user", "text": prompt})
    st.session_state.messages.append(handle(prompt))
process_pending()

messages = st.session_state.messages
if not messages:
    st.caption("Try one of these:")
    with st.container(horizontal=True):
        for ex in get_service().model.examples.values():
            st.button(ex.paraphrases[0] if ex.paraphrases else ex.question, key=f"ex_{ex.id}",
                      on_click=queue, args=({"example": ex.id},))

last_assistant = max((i for i, m in enumerate(messages) if m["role"] == "assistant"), default=-1)
for i, msg in enumerate(messages):
    with st.chat_message(msg["role"]):
        if msg["role"] == "user":
            st.markdown(msg["text"])
        elif msg["kind"] == "answer":
            render_result(msg, interactive=i == last_assistant)
        elif msg["kind"] in ("clarify", "refuse"):
            render_options(msg, interactive=i == last_assistant)
        else:
            st.warning(msg["text"])

if messages:
    st.button("New conversation", icon=":material/restart_alt:",
              on_click=lambda: st.session_state.update(messages=[], conversation=type(st.session_state.conversation)()))
