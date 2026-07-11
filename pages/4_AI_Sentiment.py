"""AI Sentiment: news sentiment scoring (Gemini, with a rule-based fallback) per ticker."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.config import DEFAULT_TICKERS, GEMINI_API_KEY, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.explain import explain_sentiment
from core.queries import get_price_history
from core.sentiment import analyze_ticker_sentiment, get_stored_sentiment
from core.ui_components import render_explanation, render_mode_toggle, stock_picker

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | AI Sentiment", page_icon="\U0001F4C8", layout="wide")
st.title("AI Sentiment")

mode = render_mode_toggle()

if not GEMINI_API_KEY:
    st.info(
        "**Fallback mode** — no `GEMINI_API_KEY` configured in `.env`. Sentiment is scored with a "
        "simple keyword-polarity model instead of Gemini. Results are directional, not authoritative."
    )


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


col_symbol, col_action = st.columns([2, 1])
with col_symbol:
    symbol = stock_picker("sentiment_symbol", default_symbol=DEFAULT_TICKERS[0])

if _load_history(symbol).empty:
    with st.spinner(f"Fetching {symbol} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {symbol}: {exc}")
            st.stop()

with col_action:
    st.write("")
    st.write("")
    if st.button("Analyze Sentiment", use_container_width=True):
        with st.spinner(f"Fetching and scoring recent news for {symbol}..."):
            new_rows = analyze_ticker_sentiment(symbol)
        if new_rows:
            st.success(f"Scored {len(new_rows)} new article(s).")
        else:
            st.info("No new articles found — showing previously stored results, if any.")

st.divider()

stored = get_stored_sentiment(symbol)

if not stored:
    st.caption(
        "No sentiment data yet for this ticker. Click **Analyze Sentiment** to fetch and score recent news. "
        "Coverage for smaller or less-followed Indian tickers can be sparse — that's expected, not an error."
    )
else:
    sentiment_df = pd.DataFrame(stored).sort_values("date")

    overall = explain_sentiment(float(sentiment_df["sentiment"].mean()))
    render_explanation(overall, mode)

    st.subheader("Sentiment Timeline")
    colors = [theme.STATUS_GOOD if s >= 0 else theme.STATUS_CRITICAL for s in sentiment_df["sentiment"]]
    fig = go.Figure(
        go.Bar(
            x=sentiment_df["date"],
            y=sentiment_df["sentiment"],
            marker_color=colors,
            customdata=sentiment_df["headline"],
            hovertemplate="%{customdata}<br>Sentiment: %{y:.2f}<extra></extra>",
        )
    )
    fig.add_hline(y=0, line_color=theme.INK_MUTED, line_width=1)
    fig.update_layout(
        yaxis_title="Sentiment (-1 to 1)",
        yaxis_range=[-1, 1],
        plot_bgcolor="white",
        margin=dict(t=10, l=10, r=10, b=10),
        height=320,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Recent Articles")
    for row in sorted(stored, key=lambda r: r["date"], reverse=True):
        sentiment_label = "Positive" if row["sentiment"] > 0.1 else "Negative" if row["sentiment"] < -0.1 else "Neutral"
        with st.container(border=True):
            st.markdown(f"**{row['headline']}**")
            st.caption(f"{row['source']} · {row['date']}")
            badge_cols = st.columns([1, 1, 4])
            badge_cols[0].metric("Sentiment", f"{row['sentiment']:+.2f}", sentiment_label)
            badge_cols[1].metric("Confidence", f"{row['confidence']:.0%}")
            badge_cols[2].write(row["summary"])

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
