"""Stock Analysis: candlestick + volume, indicator overlays, RSI/MACD subplots, key stats."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from core import theme
from core.config import DEFAULT_TICKERS, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.formatting import format_inr
from core.indicators import bollinger_bands, ema, macd, rsi, sma, volatility
from core.queries import get_price_history
from core.ui_components import stock_picker

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | Stock Analysis", page_icon="\U0001F4C8", layout="wide")
st.title("Stock Analysis")

RANGE_OPTIONS = {"1M": 21, "3M": 63, "6M": 126, "1Y": 252, "3Y": 756, "5Y": 1260}


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


col_symbol, col_range = st.columns([2, 3])
with col_symbol:
    symbol = stock_picker("stock_analysis_symbol", default_symbol=DEFAULT_TICKERS[0])

with col_range:
    range_label = st.radio("Range", options=list(RANGE_OPTIONS.keys()), index=3, horizontal=True)

history = _load_history(symbol)

if history.empty:
    with st.spinner(f"Fetching {symbol} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
            history = _load_history(symbol)
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {symbol}: {exc}")
            st.stop()

if history.empty:
    st.warning(f"No price data available for {symbol}.")
    st.stop()

st.subheader(symbol)

overlay_cols = st.columns(3)
show_sma = overlay_cols[0].checkbox("SMA (20)", value=True)
show_ema = overlay_cols[1].checkbox("EMA (20)", value=False)
show_bollinger = overlay_cols[2].checkbox("Bollinger Bands (20, 2σ)", value=False)

full_close = history["close"]
window = RANGE_OPTIONS[range_label]
visible = history.tail(window)

fig = make_subplots(
    rows=4,
    cols=1,
    shared_xaxes=True,
    row_heights=[0.5, 0.15, 0.175, 0.175],
    vertical_spacing=0.03,
    subplot_titles=("Price", "Volume", "RSI (14)", "MACD"),
)

fig.add_trace(
    go.Candlestick(
        x=visible.index,
        open=visible["open"],
        high=visible["high"],
        low=visible["low"],
        close=visible["close"],
        increasing_line_color=theme.STATUS_GOOD,
        decreasing_line_color=theme.STATUS_CRITICAL,
        name="Price",
        showlegend=False,
    ),
    row=1,
    col=1,
)

if show_sma:
    sma_series = sma(full_close, window=20).reindex(visible.index)
    fig.add_trace(
        go.Scatter(x=visible.index, y=sma_series, name="SMA 20", line=dict(color=theme.CATEGORICAL[0], width=2)),
        row=1,
        col=1,
    )
if show_ema:
    ema_series = ema(full_close, span=20).reindex(visible.index)
    fig.add_trace(
        go.Scatter(x=visible.index, y=ema_series, name="EMA 20", line=dict(color=theme.CATEGORICAL[4], width=2)),
        row=1,
        col=1,
    )
if show_bollinger:
    bands = bollinger_bands(full_close, window=20, num_std=2.0).reindex(visible.index)
    fig.add_trace(
        go.Scatter(x=visible.index, y=bands["upper"], name="Bollinger Upper", line=dict(color=theme.INK_MUTED, width=1, dash="dot")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=visible.index, y=bands["lower"], name="Bollinger Lower", line=dict(color=theme.INK_MUTED, width=1, dash="dot"), fill="tonexty", fillcolor="rgba(137,135,129,0.08)"),
        row=1,
        col=1,
    )

volume_colors = [
    theme.STATUS_GOOD if c >= o else theme.STATUS_CRITICAL for o, c in zip(visible["open"], visible["close"])
]
fig.add_trace(
    go.Bar(x=visible.index, y=visible["volume"], marker_color=volume_colors, name="Volume", showlegend=False),
    row=2,
    col=1,
)

rsi_series = rsi(full_close, window=14).reindex(visible.index)
fig.add_trace(
    go.Scatter(x=visible.index, y=rsi_series, name="RSI 14", line=dict(color=theme.CATEGORICAL[0], width=2), showlegend=False),
    row=3,
    col=1,
)
fig.add_hline(y=70, line_dash="dot", line_color=theme.STATUS_CRITICAL, row=3, col=1)
fig.add_hline(y=30, line_dash="dot", line_color=theme.STATUS_GOOD, row=3, col=1)

macd_df = macd(full_close).reindex(visible.index)
fig.add_trace(
    go.Scatter(x=visible.index, y=macd_df["macd"], name="MACD", line=dict(color=theme.CATEGORICAL[0], width=2)),
    row=4,
    col=1,
)
fig.add_trace(
    go.Scatter(x=visible.index, y=macd_df["signal"], name="Signal", line=dict(color=theme.CATEGORICAL[5], width=2)),
    row=4,
    col=1,
)
hist_colors = [theme.STATUS_GOOD if v >= 0 else theme.STATUS_CRITICAL for v in macd_df["histogram"].fillna(0)]
fig.add_trace(
    go.Bar(x=visible.index, y=macd_df["histogram"], marker_color=hist_colors, name="Histogram", showlegend=False),
    row=4,
    col=1,
)

fig.update_layout(
    height=900,
    xaxis4_rangeslider_visible=False,
    xaxis_rangeslider_visible=False,
    plot_bgcolor="white",
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(t=40, l=10, r=10, b=10),
)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Key Stats")
last_close = full_close.iloc[-1]
last_year = full_close.tail(252)
vol_series = volatility(full_close, window=20, annualize=True)
stat_cols = st.columns(5)
stat_cols[0].metric("Last Close", format_inr(last_close))
stat_cols[1].metric("52W High", format_inr(last_year.max()))
stat_cols[2].metric("52W Low", format_inr(last_year.min()))
stat_cols[3].metric("Avg Volume (20d)", f"{history['volume'].tail(20).mean():,.0f}")
stat_cols[4].metric("Volatility (ann., 20d)", f"{vol_series.iloc[-1]:.1%}" if pd.notna(vol_series.iloc[-1]) else "—")

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
