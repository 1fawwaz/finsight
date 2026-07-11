"""Market Overview: watchlist table, sector heatmap, top movers."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.data_ingestion import IngestionError, ingest_ticker
from core.database import init_db
from core.indicators import rsi
from core.market_status import get_nse_market_status
from core.queries import get_price_history, get_ticker_info
from core.ui_components import display_symbol, render_mode_toggle, stock_search_and_pick
from core.watchlist import add_to_watchlist, list_watchlist, remove_from_watchlist, seed_default_watchlist_if_empty
from core.config import get_logger

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | Market Overview", page_icon="\U0001F4C8", layout="wide")
st.title("Market Overview")

init_db()
seed_default_watchlist_if_empty()
mode = render_mode_toggle()

status = get_nse_market_status()
status_color = "green" if status.is_open else "gray"
st.caption(f":{status_color}[● {status.label}] · {status.current_time_ist.strftime('%d %b %Y, %H:%M')} IST")


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


def _rsi_badge(value: float | None, mode: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    if mode == "Simple":
        if value >= 70:
            return f"{value:.0f} · Buying fast"
        if value <= 30:
            return f"{value:.0f} · Selling fast"
        return f"{value:.0f} · Calm"
    if value >= 70:
        return f"{value:.0f} · Overbought"
    if value <= 30:
        return f"{value:.0f} · Oversold"
    return f"{value:.0f} · Neutral"


watchlist = list_watchlist()
watchlist_symbols = [w["symbol"] for w in watchlist]

col_add, col_remove = st.columns([2, 1])
with col_add:
    match = stock_search_and_pick("watchlist_add", label="Add a stock to your watchlist")
    if match is not None and st.button(f"Add {display_symbol(match.symbol)}"):
        with st.spinner(f"Fetching {display_symbol(match.symbol)} from Yahoo Finance..."):
            try:
                added, message = add_to_watchlist(match.symbol)
                _load_history.clear()
                _load_info.clear()
                (st.success if added else st.info)(message)
                st.session_state.pop("watchlist_add_query", None)
                st.session_state.pop("watchlist_add_choice", None)
                st.rerun()
            except IngestionError as exc:
                st.warning(f"Couldn't fetch {display_symbol(match.symbol)}: {exc}")

with col_remove:
    if watchlist_symbols:
        to_remove = st.selectbox("Remove a stock", options=[""] + watchlist_symbols, format_func=lambda s: display_symbol(s) if s else "")
        if to_remove and st.button("Remove"):
            remove_from_watchlist(to_remove)
            st.rerun()

st.divider()

rows = []
for symbol in watchlist_symbols:
    history = _load_history(symbol)
    if history.empty:
        continue
    close = history["close"]
    info = _load_info(symbol) or {}
    latest_rsi = rsi(close, window=14).iloc[-1] if len(close) >= 15 else None
    rows.append(
        {
            "Symbol": display_symbol(symbol),
            "Name": info.get("name") or symbol,
            "Sector": info.get("sector") or "Unknown",
            "Price": close.iloc[-1],
            "1D %": _pct_change(close, 1),
            "1W %": _pct_change(close, 5),
            "1M %": _pct_change(close, 21),
            "RSI (14)" if mode == "Professional" else "Buying/Selling": _rsi_badge(latest_rsi, mode),
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
            theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=380)
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
            theme.apply_dark_layout(
                fig,
                margin=dict(t=10, l=10, r=10, b=10),
                height=380,
                xaxis_title="1D % Change",
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
