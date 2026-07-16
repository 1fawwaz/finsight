"""FinSight entrypoint: a trading-terminal-style home dashboard.

Market status, portfolio value, watchlist, top movers, an AI market summary, recent
searches, and a quick search box -- everything a returning user needs at a glance.
"""

import pandas as pd
import streamlit as st

from core.config import BENCHMARK_BANKNIFTY, BENCHMARK_NIFTY50, BENCHMARK_SENSEX, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.database import init_db
from core.design import inject_design_system
from core.formatting import format_inr
from core.market_status import get_nse_market_status
from core.market_summary import MarketSnapshot, summarize_market
from core.portfolio import list_holdings, list_portfolios
from core.queries import get_price_history
from core.ui_components import (
    RECENT_SEARCHES_KEY,
    display_symbol,
    render_empty_state,
    render_live_index_cards,
    render_mode_toggle,
    render_page_header,
    stock_search_and_pick,
)
from core.watchlist import list_watchlist, seed_default_watchlist_if_empty

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight", page_icon="\U0001F4C8", layout="wide")
inject_design_system()

init_db()
seed_default_watchlist_if_empty()
mode = render_mode_toggle()

render_page_header("FinSight", "AI Finance & Trading Intelligence Platform · Indian markets (NSE/BSE)")


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


def _ensure_history(symbol: str) -> pd.DataFrame:
    history = _load_history(symbol)
    if history.empty:
        try:
            ingest_ticker(symbol)
            _load_history.clear()
            history = _load_history(symbol)
        except IngestionError as exc:
            logger.warning("Could not ingest %s for home dashboard: %s", symbol, exc)
            return pd.DataFrame()
    return history


def _pct_change(close: pd.Series, periods: int = 1) -> float | None:
    if len(close) <= periods:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - periods] - 1)


status = get_nse_market_status()
status_color = "green" if status.is_open else "gray"
st.caption(f":{status_color}[● {status.label}] · {status.current_time_ist.strftime('%d %b %Y, %H:%M')} IST")
if not status.is_open:
    next_session_label = "today" if status.next_trading_day == status.current_time_ist.date() else status.next_trading_day.strftime("%A, %d %b")
    st.caption(f"Next trading session: {next_session_label}")

index_specs = [("Nifty 50", BENCHMARK_NIFTY50), ("Sensex", BENCHMARK_SENSEX), ("Bank Nifty", BENCHMARK_BANKNIFTY)]
index_pcts: dict[str, float | None] = {}
index_last_close: dict[str, float] = {}
for label, symbol in index_specs:
    with st.spinner(f"Loading {label}..."):
        history = _ensure_history(symbol)
    if history.empty:
        continue
    close = history["close"]
    pct = _pct_change(close, 1)
    index_pcts[symbol] = pct
    index_last_close[symbol] = float(close.iloc[-1])

# Live-updating when the active broker's credentials are configured (routed via
# core/broker_adapter.py::get_active_broker_adapter() -- Upstox or Kotak Neo,
# whichever USE_UPSTOX_PRIMARY selects); falls back to the historical values just
# computed above when a live tick isn't available yet -- historical computation
# itself (index_pcts, used later for the AI market summary) is unchanged either way.
render_live_index_cards(index_specs, index_last_close, index_pcts)

st.divider()

col_portfolio, col_watchlist = st.columns([1, 2])

with col_portfolio:
    st.subheader("Portfolio Value")
    total_value = 0.0
    any_holdings = False
    for portfolio in list_portfolios():
        for holding in list_holdings(portfolio["id"]):
            holding_history = _load_history(holding["symbol"])
            if not holding_history.empty:
                total_value += float(holding_history["close"].iloc[-1]) * holding["shares"]
                any_holdings = True
    if any_holdings:
        st.metric("Across all portfolios" if mode == "Professional" else "What your holdings are worth", format_inr(total_value))
    else:
        render_empty_state("No holdings yet", "Add your first stock on the Portfolio page to see its value here.", icon="\U0001F4BC")
    st.page_link("pages/3_Portfolio.py", label="Go to Portfolio →")

watchlist_rows: list[dict] = []
with col_watchlist:
    st.subheader("Watchlist")
    for entry in list_watchlist():
        watchlist_history = _load_history(entry["symbol"])
        if watchlist_history.empty:
            continue
        watchlist_close = watchlist_history["close"]
        watchlist_rows.append(
            {
                "Symbol": display_symbol(entry["symbol"]),
                "Price": watchlist_close.iloc[-1],
                "1D %": _pct_change(watchlist_close, 1),
            }
        )
    if watchlist_rows:
        watchlist_df = pd.DataFrame(watchlist_rows)
        display_watchlist_df = watchlist_df.copy()
        display_watchlist_df["1D %"] = display_watchlist_df["1D %"] * 100
        st.dataframe(
            display_watchlist_df,
            use_container_width=True,
            hide_index=True,
            height=215,
            column_config={
                "Price": st.column_config.NumberColumn(format="₹%.2f"),
                "1D %": st.column_config.NumberColumn(format="%.2f%%"),
            },
        )
    else:
        render_empty_state("Your watchlist is empty", "Add a stock on the Market Overview page to track it here.", icon="\U0001F440")
    st.page_link("pages/1_Market_Overview.py", label="Manage watchlist →")

st.divider()

st.subheader("Today's Market, in Brief" if mode == "Simple" else "AI Market Summary")
top_gainer = None
top_loser = None
if watchlist_rows:
    movers_df = pd.DataFrame(watchlist_rows).dropna(subset=["1D %"])
    if not movers_df.empty:
        best = movers_df.loc[movers_df["1D %"].idxmax()]
        worst = movers_df.loc[movers_df["1D %"].idxmin()]
        top_gainer = (str(best["Symbol"]), float(best["1D %"]))
        top_loser = (str(worst["Symbol"]), float(worst["1D %"]))

@st.cache_data(ttl=1800, show_spinner=False)
def _cached_market_summary(
    nifty_pct: float | None,
    sensex_pct: float | None,
    banknifty_pct: float | None,
    top_gainer: tuple[str, float] | None,
    top_loser: tuple[str, float] | None,
) -> tuple[str, bool]:
    snapshot = MarketSnapshot(
        nifty_pct=nifty_pct, sensex_pct=sensex_pct, banknifty_pct=banknifty_pct, top_gainer=top_gainer, top_loser=top_loser
    )
    return summarize_market(snapshot)


summary_text, used_gemini = _cached_market_summary(
    index_pcts.get(BENCHMARK_NIFTY50), index_pcts.get(BENCHMARK_SENSEX), index_pcts.get(BENCHMARK_BANKNIFTY), top_gainer, top_loser
)
st.info(summary_text)
if not used_gemini and mode == "Professional":
    st.caption("Rule-based summary -- Gemini unavailable or not configured.")

st.divider()

col_recent, col_quick = st.columns([1, 1])
with col_recent:
    st.subheader("Recent Searches")
    recent_searches: list[str] = st.session_state.get(RECENT_SEARCHES_KEY, [])
    if recent_searches:
        for symbol in recent_searches:
            st.markdown(f"- **{display_symbol(symbol)}**")
    else:
        render_empty_state("No recent searches", "Search for a stock anywhere in the app and it'll show up here.", icon="\U0001F50D")

with col_quick:
    st.subheader("Quick Search")
    quick_match = stock_search_and_pick("home_quick_search", label="Find a stock")
    if quick_match is not None:
        st.session_state["stock_analysis_symbol"] = quick_match.symbol
        st.page_link(
            "pages/2_Stock_Analysis.py",
            label=f"Open {display_symbol(quick_match.symbol)} in Stock Analysis →",
        )

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
