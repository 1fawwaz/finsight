"""Portfolio: CRUD holdings, allocation, cumulative return vs an NSE benchmark, risk metrics, correlation."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.config import BENCHMARKS, UNSUPPORTED_MARKET_MESSAGE, get_logger, is_supported_symbol
from core.data_ingestion import IngestionError, ingest_ticker
from core.formatting import format_inr
from core.portfolio import (
    add_holding,
    correlation_matrix,
    create_portfolio,
    delete_holding,
    list_holdings,
    list_portfolios,
    max_drawdown,
    portfolio_daily_returns,
    portfolio_weights,
    sharpe_ratio,
)
from core.queries import get_multi_symbol_close, get_price_history

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | Portfolio", page_icon="\U0001F4C8", layout="wide")
st.title("Portfolio")


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


def _ensure_ingested(symbol: str) -> bool:
    if not _load_history(symbol).empty:
        return True
    with st.spinner(f"Fetching {symbol} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
            return True
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {symbol}: {exc}")
            return False


portfolios = list_portfolios()
portfolio_names = {p["name"]: p["id"] for p in portfolios}
options = ["<none>"] + list(portfolio_names.keys())

if "selected_portfolio" not in st.session_state:
    st.session_state.selected_portfolio = "<none>"

col_select, col_create = st.columns([2, 2])
with col_select:
    default_index = options.index(st.session_state.selected_portfolio) if st.session_state.selected_portfolio in options else 0
    selected_name = st.selectbox("Portfolio", options=options, index=default_index)
    st.session_state.selected_portfolio = selected_name
with col_create:
    with st.form("create_portfolio_form", clear_on_submit=True):
        new_portfolio_name = st.text_input("Or create a new portfolio", placeholder="e.g. Core Holdings")
        if st.form_submit_button("Create Portfolio") and new_portfolio_name.strip():
            create_portfolio(new_portfolio_name.strip())
            st.session_state.selected_portfolio = new_portfolio_name.strip()
            st.rerun()

if selected_name == "<none>":
    st.info("Create or select a portfolio to get started.")
    st.stop()

portfolio_id = portfolio_names[selected_name]

st.divider()
st.subheader("Holdings")

with st.form("add_holding_form", clear_on_submit=True):
    form_cols = st.columns([2, 1, 1, 1])
    symbol_input = form_cols[0].text_input("Symbol", placeholder="e.g. WIPRO.NS").strip().upper()
    shares_input = form_cols[1].number_input("Shares", min_value=0.0, step=1.0)
    cost_input = form_cols[2].number_input("Avg cost (₹)", min_value=0.0, step=0.01)
    submitted = form_cols[3].form_submit_button("Add Holding")
    if submitted:
        if not symbol_input or shares_input <= 0:
            st.warning("Enter a symbol and a positive share count.")
        elif not is_supported_symbol(symbol_input):
            st.warning(UNSUPPORTED_MARKET_MESSAGE)
        elif _ensure_ingested(symbol_input):
            add_holding(portfolio_id, symbol_input, shares_input, cost_input)
            st.rerun()

holdings = list_holdings(portfolio_id)

if not holdings:
    st.info("No holdings yet. Add one above.")
    st.stop()

current_prices: dict[str, float] = {}
for h in holdings:
    history = _load_history(h["symbol"])
    if not history.empty:
        current_prices[h["symbol"]] = float(history["close"].iloc[-1])

holdings_rows = []
for h in holdings:
    price = current_prices.get(h["symbol"])
    market_value = price * h["shares"] if price is not None else None
    holdings_rows.append(
        {
            "id": h["id"],
            "Symbol": h["symbol"],
            "Shares": h["shares"],
            "Avg Cost": h["avg_cost"],
            "Current Price": price,
            "Market Value": market_value,
            "Gain/Loss %": (price / h["avg_cost"] - 1) if price and h["avg_cost"] else None,
        }
    )

holdings_df = pd.DataFrame(holdings_rows)
display_holdings_df = holdings_df.drop(columns=["id"])
display_holdings_df["Gain/Loss %"] = display_holdings_df["Gain/Loss %"] * 100
st.dataframe(
    display_holdings_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Avg Cost": st.column_config.NumberColumn(format="₹%.2f"),
        "Current Price": st.column_config.NumberColumn(format="₹%.2f"),
        "Market Value": st.column_config.NumberColumn(format="₹%.2f"),
        "Gain/Loss %": st.column_config.NumberColumn(format="%.2f%%"),
    },
)

delete_choice = st.selectbox("Remove a holding", options=["<none>"] + [f"{r['Symbol']} (id {r['id']})" for r in holdings_rows])
if delete_choice != "<none>" and st.button("Delete Holding"):
    holding_id = int(delete_choice.split("id ")[1].rstrip(")"))
    delete_holding(holding_id)
    st.rerun()

valid_symbols = [h["symbol"] for h in holdings if h["symbol"] in current_prices]
if not valid_symbols:
    st.warning("No price data available for the current holdings.")
    st.stop()

shares_map = {h["symbol"]: h["shares"] for h in holdings if h["symbol"] in current_prices}
weights = portfolio_weights(shares_map, current_prices)

st.divider()
col_alloc, col_metrics = st.columns([1, 1])

with col_alloc:
    st.subheader("Allocation")
    fig = go.Figure(
        go.Pie(
            labels=list(weights.keys()),
            values=list(weights.values()),
            marker=dict(colors=theme.CATEGORICAL[: len(weights)]),
            hole=0.4,
        )
    )
    fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=350)
    st.plotly_chart(fig, use_container_width=True)

price_df = get_multi_symbol_close(valid_symbols)

with col_metrics:
    st.subheader("Risk Metrics")
    if len(price_df) < 2:
        st.caption("Not enough overlapping history to compute risk metrics.")
    else:
        daily_returns = portfolio_daily_returns(price_df, weights)
        portfolio_value = (1 + daily_returns).cumprod()
        sharpe = sharpe_ratio(daily_returns)
        drawdown = max_drawdown(portfolio_value)
        metric_cols = st.columns(2)
        metric_cols[0].metric("Sharpe Ratio", f"{sharpe:.2f}")
        metric_cols[1].metric("Max Drawdown", f"{drawdown:.1%}")

st.divider()
benchmark_label = st.radio("Benchmark", options=list(BENCHMARKS.keys()), index=0, horizontal=True)
benchmark_symbol = BENCHMARKS[benchmark_label]
st.subheader(f"Cumulative Return vs {benchmark_label}")
if _ensure_ingested(benchmark_symbol) and len(price_df) >= 2:
    combined = get_multi_symbol_close(valid_symbols + [benchmark_symbol])
    if len(combined) >= 2:
        portfolio_returns = portfolio_daily_returns(combined[valid_symbols], weights)
        portfolio_cum = (1 + portfolio_returns).cumprod() - 1
        benchmark_cum = (combined[benchmark_symbol] / combined[benchmark_symbol].iloc[0]) - 1

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=portfolio_cum.index, y=portfolio_cum * 100, name="Portfolio", line=dict(color=theme.CATEGORICAL[0], width=2)))
        fig.add_trace(go.Scatter(x=benchmark_cum.index, y=benchmark_cum * 100, name=benchmark_label, line=dict(color=theme.INK_MUTED, width=2, dash="dot")))
        fig.update_layout(
            yaxis_title="Cumulative Return (%)",
            plot_bgcolor="white",
            margin=dict(t=10, l=10, r=10, b=10),
            height=380,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("Not enough overlapping history with the benchmark yet.")

if len(valid_symbols) >= 2 and len(price_df) >= 2:
    st.divider()
    st.subheader("Correlation Matrix")
    corr = correlation_matrix(price_df)
    fig = go.Figure(
        go.Heatmap(
            z=corr.values,
            x=corr.columns,
            y=corr.index,
            colorscale=theme.DIVERGING_BLUE_RED,
            zmid=0,
            zmin=-1,
            zmax=1,
            text=corr.round(2).values,
            texttemplate="%{text}",
        )
    )
    fig.update_layout(margin=dict(t=10, l=10, r=10, b=10), height=400)
    st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
