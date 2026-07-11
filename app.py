"""FinSight entrypoint: landing page and shared footer disclaimer."""

import streamlit as st

from core.database import init_db
from core.ui_components import render_mode_toggle

st.set_page_config(page_title="FinSight", page_icon="\U0001F4C8", layout="wide")

init_db()
render_mode_toggle()

st.title("FinSight")
st.subheader("AI Finance & Trading Intelligence Platform")

st.markdown(
    """
Use the sidebar to navigate:

- **Market Overview** — watchlist, sector heatmap, top movers
- **Stock Analysis** — candlestick charts with technical indicators
- **Portfolio** — track holdings, allocation, risk metrics
- **AI Sentiment** — news sentiment scoring
- **ML Signals** — direction classifier + backtest
- **About** — architecture and disclaimer
"""
)

st.divider()
st.caption(
    "FinSight is a signal-research and education tool. Nothing shown here is financial advice."
)
