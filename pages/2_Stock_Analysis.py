"""Stock Analysis: candlestick + volume, indicator overlays, RSI/MACD subplots, key stats."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from core import theme
from core.config import DEFAULT_TICKERS, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.design import inject_design_system
from core.explain import (
    explain_adx,
    explain_atr,
    explain_bollinger,
    explain_macd,
    explain_resistance,
    explain_rsi,
    explain_support,
    explain_volatility,
    explain_vwap,
)
from core.formatting import format_inr
from core.indicators import adx, atr, bollinger_bands, ema, macd, rsi, sma, support_resistance, volatility, vwap
from core.queries import get_price_history
from core.ui_components import display_symbol, render_ai_panel, render_explanation, render_mode_toggle, render_page_header, stock_picker

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | Stock Analysis", page_icon="\U0001F4C8", layout="wide")
inject_design_system()
render_page_header("Stock Analysis", "Candlestick charting, indicator overlays, and key technical stats.")

mode = render_mode_toggle()

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
    with st.spinner(f"Fetching {display_symbol(symbol)} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
            history = _load_history(symbol)
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {display_symbol(symbol)}: {exc}")
            st.stop()

if history.empty:
    st.warning(f"No price data available for {display_symbol(symbol)}.")
    st.stop()

st.subheader(display_symbol(symbol))

if mode == "Professional":
    overlay_cols = st.columns(5)
    show_sma = overlay_cols[0].checkbox("SMA (20)", value=True)
    show_ema = overlay_cols[1].checkbox("EMA (20)", value=False)
    show_bollinger = overlay_cols[2].checkbox("Bollinger Bands (20, 2σ)", value=False)
    show_vwap = overlay_cols[3].checkbox("VWAP (20d rolling)", value=False)
    show_support_resistance = overlay_cols[4].checkbox("Support / Resistance", value=False)
else:
    overlay_cols = st.columns(3)
    show_sma = overlay_cols[0].checkbox("Trend line (20-day average)", value=True)
    show_ema = overlay_cols[1].checkbox("Faster trend line", value=False)
    show_bollinger = overlay_cols[2].checkbox("Normal price range", value=False)
    show_vwap = False
    show_support_resistance = False

full_close = history["close"]
full_high = history["high"]
full_low = history["low"]
full_volume = history["volume"]
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
        go.Scatter(x=visible.index, y=bands["upper"], name="Bollinger Upper", line=dict(color=theme.DARK_INK_MUTED, width=1, dash="dot")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=visible.index, y=bands["lower"], name="Bollinger Lower", line=dict(color=theme.DARK_INK_MUTED, width=1, dash="dot"), fill="tonexty", fillcolor="rgba(125,130,144,0.12)"),
        row=1,
        col=1,
    )
if show_vwap:
    vwap_series = vwap(full_high, full_low, full_close, full_volume, window=20).reindex(visible.index)
    fig.add_trace(
        go.Scatter(x=visible.index, y=vwap_series, name="VWAP (20d)", line=dict(color=theme.CATEGORICAL[2], width=2, dash="dash")),
        row=1,
        col=1,
    )
if show_support_resistance:
    sr = support_resistance(full_high, full_low, window=20).reindex(visible.index)
    fig.add_trace(
        go.Scatter(x=visible.index, y=sr["resistance"], name="Resistance", line=dict(color=theme.STATUS_CRITICAL, width=1, dash="dot")),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(x=visible.index, y=sr["support"], name="Support", line=dict(color=theme.STATUS_GOOD, width=1, dash="dot")),
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

theme.apply_dark_layout(
    fig,
    height=900,
    xaxis4_rangeslider_visible=False,
    xaxis_rangeslider_visible=False,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    margin=dict(t=40, l=10, r=10, b=10),
)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Key Stats")
last_close = full_close.iloc[-1]
last_year = full_close.tail(252)
vol_series = volatility(full_close, window=20, annualize=True)
atr_series = atr(full_high, full_low, full_close, window=14)
adx_series = adx(full_high, full_low, full_close, window=14)
latest_vol = vol_series.iloc[-1] if pd.notna(vol_series.iloc[-1]) else None
latest_atr = atr_series.iloc[-1] if pd.notna(atr_series.iloc[-1]) else None
latest_adx = adx_series.iloc[-1] if pd.notna(adx_series.iloc[-1]) else None

stat_cols = st.columns(4)
stat_cols[0].metric("Last Close", format_inr(last_close))
stat_cols[1].metric("52W High", format_inr(last_year.max()))
stat_cols[2].metric("52W Low", format_inr(last_year.min()))
stat_cols[3].metric("Avg Volume (20d)", f"{history['volume'].tail(20).mean():,.0f}")

stat_cols_2 = st.columns(4)
stat_cols_2[0].metric("Volatility (ann., 20d)" if mode == "Professional" else "How bumpy (yearly)", f"{latest_vol:.1%}" if latest_vol is not None else "—")
stat_cols_2[1].metric("ATR (14)" if mode == "Professional" else "Typical daily swing", format_inr(latest_atr) if latest_atr is not None else "—")
stat_cols_2[2].metric("ADX (14)" if mode == "Professional" else "Trend strength", f"{latest_adx:.0f}" if latest_adx is not None else "—")
latest_rsi_value = rsi(full_close, window=14).iloc[-1]
stat_cols_2[3].metric("RSI (14)" if mode == "Professional" else "Buying/selling speed", f"{latest_rsi_value:.0f}" if pd.notna(latest_rsi_value) else "—")

st.subheader("What does this mean?" if mode == "Simple" else "Indicator Notes")
macd_latest = macd_df.iloc[-1] if not macd_df.empty else None
explanations = [
    explain_rsi(latest_rsi_value if pd.notna(latest_rsi_value) else None),
    explain_macd(
        macd_latest["macd"] if macd_latest is not None and pd.notna(macd_latest["macd"]) else None,
        macd_latest["signal"] if macd_latest is not None and pd.notna(macd_latest["signal"]) else None,
    ),
    explain_volatility(latest_vol),
    explain_atr(latest_atr, float(last_close)),
    explain_adx(latest_adx),
]
if show_bollinger:
    latest_bands = bands.dropna().iloc[-1] if not bands.dropna().empty else None
    explanations.append(
        explain_bollinger(
            float(last_close),
            float(latest_bands["upper"]) if latest_bands is not None else None,
            float(latest_bands["lower"]) if latest_bands is not None else None,
        )
    )
if show_vwap:
    latest_vwap = vwap_series.dropna()
    explanations.append(explain_vwap(float(last_close), float(latest_vwap.iloc[-1]) if not latest_vwap.empty else None))
if show_support_resistance:
    latest_sr = sr.dropna()
    if not latest_sr.empty:
        explanations.append(explain_support(float(last_close), float(latest_sr["support"].iloc[-1])))
        explanations.append(explain_resistance(float(last_close), float(latest_sr["resistance"].iloc[-1])))

for explanation in explanations:
    render_explanation(explanation, mode)

st.divider()
ai_panel_data = {
    "symbol": display_symbol(symbol),
    "last_close": round(float(last_close), 2),
    "rsi_14": round(float(latest_rsi_value), 1) if pd.notna(latest_rsi_value) else None,
    "macd": round(float(macd_latest["macd"]), 2) if macd_latest is not None and pd.notna(macd_latest["macd"]) else None,
    "macd_signal": round(float(macd_latest["signal"]), 2) if macd_latest is not None and pd.notna(macd_latest["signal"]) else None,
    "volatility_annualized": round(float(latest_vol), 3) if latest_vol is not None else None,
    "atr_14": round(float(latest_atr), 2) if latest_atr is not None else None,
    "adx_14": round(float(latest_adx), 1) if latest_adx is not None else None,
    "52w_high": round(float(last_year.max()), 2),
    "52w_low": round(float(last_year.min()), 2),
}
ai_fallback = " ".join((e.simple if mode == "Simple" else e.professional) for e in explanations)
render_ai_panel(f"Technical analysis for {display_symbol(symbol)}", ai_panel_data, ai_fallback, mode)

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
