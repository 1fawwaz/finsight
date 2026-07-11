"""Ask FinSight AI: a chat grounded in the app's own data (prices, indicators, portfolio
metrics, news sentiment, ML signals) -- not generic LLM knowledge."""

import streamlit as st

from core.chat import answer_question
from core.database import init_db
from core.ui_components import render_mode_toggle, render_prediction_disclaimer

st.set_page_config(page_title="FinSight | Ask FinSight AI", page_icon="\U0001F4C8", layout="wide")
st.title("Ask FinSight AI")

init_db()
mode = render_mode_toggle()

st.caption(
    "Ask about any NSE-listed stock, your portfolio, or today's market. Answers are grounded "
    "in FinSight's own data (prices, indicators, sentiment, ML signals) -- not generic knowledge."
)
render_prediction_disclaimer()

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


def _ask(question: str) -> None:
    st.session_state.chat_history.append({"role": "user", "content": question})
    with st.spinner("Thinking..."):
        answer, _used_gemini = answer_question(question, mode)
    st.session_state.chat_history.append({"role": "assistant", "content": answer})


for message in st.session_state.chat_history:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if not st.session_state.chat_history:
    st.caption("Try asking:")
    example_queries = [
        "Should I buy TCS?",
        "Explain Reliance like I'm a beginner",
        "Why is Infosys falling today?",
        "Compare TCS vs Infosys",
        "Summarize today's market in under 30 seconds",
    ]
    example_cols = st.columns(len(example_queries))
    for col, example in zip(example_cols, example_queries):
        if col.button(example, use_container_width=True):
            _ask(example)
            st.rerun()

typed_question = st.chat_input("Ask about a stock, your portfolio, or the market...")
if typed_question:
    _ask(typed_question)
    st.rerun()

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
