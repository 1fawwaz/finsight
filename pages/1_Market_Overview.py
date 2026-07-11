"""Market Overview: watchlist table, sector heatmap, top movers."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.config import DEFAULT_TICKERS, UNSUPPORTED_MARKET_MESSAGE, get_logger, is_supported_symbol
from core.data_ingestion import IngestionError, ingest_ticker
from core.indicators import rsi
from core.market_status import get_nse_market_status
from core.queries import get_price_history, get_ticker_info

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | Market Overview", page_icon="\U0001F4C8", layout="wide")
st.title("Market Overview")

status = get_nse_market_status()
status_color = "green" if status.is_open else "gray"
st.caption(f":{status_color}[● {status.label}] · {status.current_time_ist.strftime('%d %b %Y, %H:%M')} IST")

if "watchlist" not in st.session_state:
    st.session_state.watchlist = list(DEFAULT_TICKERS)


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _load_info(symbol: str) -> dict | None:
    return get_ticker_info(symbol)


def _pct_change(close: pd.Series, periods: int) -> float | None:
    if len(close) <= periods:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - periods] - 1)


def _rsi_badge(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    if value >= 70:
        return f"{value:.0f} · Overbought"
    if value <= 30:
        return f"{value:.0f} · Oversold"
    return f"{value:.0f} · Neutral"


col_add, col_remove = st.columns([2, 1])
with col_add:
    new_symbol = st.text_input("Add a ticker to your watchlist", placeholder="e.g. WIPRO.NS").strip().upper()
    if st.button("Add") and new_symbol:
        if not is_supported_symbol(new_symbol):
            st.warning(UNSUPPORTED_MARKET_MESSAGE)
        elif new_symbol in st.session_state.watchlist:
            st.info(f"{new_symbol} is already in your watchlist.")
        else:
            info = _load_info(new_symbol)
            if info is None:
                with st.spinner(f"Fetching {new_symbol} from Yahoo Finance..."):
                    try:
                        ingest_ticker(new_symbol)
                        _load_info.clear()
                        _load_history.clear()
                        st.session_state.watchlist.append(new_symbol)
                        st.rerun()
                    except IngestionError as exc:
                        st.warning(f"Couldn't fetch {new_symbol}: {exc}")
            else:
                st.session_state.watchlist.append(new_symbol)
                st.rerun()

with col_remove:
    if st.session_state.watchlist:
        to_remove = st.selectbox("Remove a ticker", options=[""] + st.session_state.watchlist)
        if to_remove and st.button("Remove"):
            st.session_state.watchlist.remove(to_remove)
            st.rerun()

st.divider()

rows = []
for symbol in st.session_state.watchlist:
    history = _load_history(symbol)
    if history.empty:
        continue
    close = history["close"]
    info = _load_info(symbol) or {}
    latest_rsi = rsi(close, window=14).iloc[-1] if len(close) >= 15 else None
    rows.append(
        {
            "Symbol": symbol,
            "Name": info.get("name") or symbol,
            "Sector": info.get("sector") or "Unknown",
            "Price": close.iloc[-1],
            "1D %": _pct_change(close, 1),
            "1W %": _pct_change(close, 5),
            "1M %": _pct_change(close, 21),
            "RSI (14)": _rsi_badge(latest_rsi),
        }
    )

if not rows:
    st.warning("No data available for the current watchlist yet.")
else:
    table_df = pd.DataFrame(rows)

    st.subheader("Watchlist")
    display_df = table_df.copy()
    display_df[["1D %", "1W %", "1M %"]] = display_df[["1D %", "1W %", "1M %"]] * 100
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn(format="₹%.2f"),
            "1D %": st.column_config.NumberColumn(format="%.2f%%"),
            "1W %": st.column_config.NumberColumn(format="%.2f%%"),
            "1M %": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )

    col_heatmap, col_movers = st.columns(2)

    with col_heatmap:
        st.subheader("Sector Heatmap (1D %)")
        sector_df = table_df.dropna(subset=["1D %"])
        if sector_df.empty:
            st.caption("Not enough data for a heatmap yet.")
        else:
            display_pct = sector_df["1D %"] * 100
            max_abs = max(display_pct.abs().max(), 1e-6)
            sectors = sector_df["Sector"].unique().tolist()
            root = "Market"

            labels = [root] + sectors + sector_df["Symbol"].tolist()
            parents = [""] + [root] * len(sectors) + sector_df["Sector"].tolist()
            values = [0] * (1 + len(sectors)) + sector_df["Price"].tolist()
            colors = [0] * (1 + len(sectors)) + display_pct.tolist()
            text = [""] * (1 + len(sectors)) + [f"{v:+.2f}%" for v in display_pct]

            fig = go.Figure(
                go.Treemap(
                    labels=labels,
                    parents=parents,
                    values=values,
                    branchvalues="remainder",
                    text=text,
                    textinfo="label+text",
                    marker=dict(
                        colors=colors,
                        colorscale=theme.DIVERGING_BLUE_RED,
                        cmid=0,
                        cmin=-max_abs,
                        cmax=max_abs,
                        colorbar=dict(title="1D %"),
                    ),
                )
            )
            fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=380)
            st.plotly_chart(fig, use_container_width=True)

    with col_movers:
        st.subheader("Top Movers (1D %)")
        movers_df = table_df.dropna(subset=["1D %"]).sort_values("1D %", ascending=False)
        if movers_df.empty:
            st.caption("Not enough data for movers yet.")
        else:
            movers_pct = movers_df["1D %"] * 100
            colors = [theme.STATUS_GOOD if v >= 0 else theme.STATUS_CRITICAL for v in movers_pct]
            fig = go.Figure(
                go.Bar(
                    x=movers_pct,
                    y=movers_df["Symbol"],
                    orientation="h",
                    marker_color=colors,
                    text=[f"{v:+.2f}%" for v in movers_pct],
                    textposition="outside",
                )
            )
            fig.update_layout(
                margin=dict(t=10, l=10, r=10, b=10),
                height=380,
                xaxis_title="1D % Change",
                yaxis=dict(autorange="reversed"),
                plot_bgcolor="white",
            )
            st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
