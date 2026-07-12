"""Ask FinSight AI: a chat grounded in the app's own data (prices, indicators, portfolio
metrics, news sentiment, ML signals) -- not generic LLM knowledge."""

import streamlit as st

from core.chat import ConversationMemory, answer_question
from core.database import init_db
from core.ui_components import render_mode_toggle, render_prediction_disclaimer

st.set_page_config(page_title="FinSight | Ask FinSight AI", page_icon="\U0001F4C8", layout="wide")
st.title("Ask FinSight AI")

init_db()
mode = render_mode_toggle()

st.caption(
    "Ask about any NSE-listed stock, your portfolio, or today's market. Answers are grounded "
    "in FinSight's own data (prices, indicators, fundamentals, sentiment, ML signals) -- not "
    "generic knowledge -- and follow-ups like \"What about Infosys?\" remember what you were "
    "just discussing."
)
render_prediction_disclaimer()

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "chat_memory" not in st.session_state:
    st.session_state.chat_memory = ConversationMemory()

col_clear, _ = st.columns([1, 5])
if col_clear.button("New conversation") and st.session_state.chat_history:
    st.session_state.chat_history = []
    st.session_state.chat_memory = ConversationMemory()
    st.rerun()


def _ask(question: str) -> None:
    st.session_state.chat_history.append({"role": "user", "content": question})
    with st.spinner("Thinking..."):
        answer, _used_gemini, updated_memory = answer_question(question, mode, st.session_state.chat_memory)
    st.session_state.chat_memory = updated_memory
    st.session_state.chat_history.append({"role": "assistant", "content": answer})


for message in st.session_state.chat_history:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if not st.session_state.chat_history:
    st.caption("Try asking:")
    example_queries = [
        "Analyze Wipro",
        "Predict Wipro tomorrow",
        "Compare TCS vs Infosys",
        "Review portfolio",
        "Explain RSI",
        "How is the market today?",
        "Why is Reliance falling?",
        "Best IT stock?",
    ]
    example_cols = st.columns(4)
    for i, example in enumerate(example_queries):
        if example_cols[i % 4].button(example, use_container_width=True):
            _ask(example)
            st.rerun()

typed_question = st.chat_input("Ask about a stock, your portfolio, or the market...")
if typed_question:
    _ask(typed_question)
    st.rerun()

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
