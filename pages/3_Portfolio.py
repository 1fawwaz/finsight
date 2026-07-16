"""Portfolio: CRUD holdings (with CSV import/export), allocation, sector allocation,
diversification score, risk meter, cumulative return vs an NSE benchmark, risk metrics,
correlation, and a bootstrap Monte Carlo simulation of future portfolio value."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.config import BENCHMARKS, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.database import init_db
from core.design import inject_design_system
from core.explain import explain_diversification, explain_drawdown, explain_risk_level, explain_sharpe
from core.formatting import format_inr
from core.portfolio import (
    add_holding,
    aggregate_shares_by_symbol,
    correlation_matrix,
    create_portfolio,
    delete_holding,
    delete_portfolio,
    DuplicatePortfolioNameError,
    diversification_score,
    list_holdings,
    list_portfolios,
    max_drawdown,
    monte_carlo_simulation,
    portfolio_daily_returns,
    portfolio_volatility,
    portfolio_weights,
    risk_level,
    sector_allocation,
    sharpe_ratio,
    update_holding,
)
from core.queries import get_multi_symbol_close, get_price_history, get_ticker_info
from core.ui_components import (
    display_symbol,
    render_ai_panel,
    render_empty_state,
    render_explanation,
    render_mode_toggle,
    render_page_header,
    reset_stock_search,
    stock_search_and_pick,
)
from core.universe import resolve_symbol

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | Portfolio", page_icon="\U0001F4C8", layout="wide")
inject_design_system()
render_page_header("Portfolio", "Track holdings, allocation, and risk across your own portfolios.")

init_db()
mode = render_mode_toggle()


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _load_info(symbol: str) -> dict | None:
    return get_ticker_info(symbol)


def _ensure_ingested(symbol: str) -> bool:
    if not _load_history(symbol).empty:
        return True
    with st.spinner(f"Fetching {display_symbol(symbol)} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
            return True
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {display_symbol(symbol)}: {exc}")
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
            try:
                create_portfolio(new_portfolio_name.strip())
                st.session_state.selected_portfolio = new_portfolio_name.strip()
                st.rerun()
            except DuplicatePortfolioNameError as exc:
                st.error(str(exc))

if selected_name == "<none>":
    render_empty_state("No portfolio selected", "Create or select a portfolio above to get started.", icon="\U0001F4BC")
    st.stop()

portfolio_id = portfolio_names[selected_name]

with st.expander("Delete this portfolio"):
    st.warning(
        f"This will permanently delete **{selected_name}**, all its holdings, and its "
        "statistics. This action cannot be undone."
    )
    confirm_delete = st.checkbox(f"I understand — permanently delete '{selected_name}'", key="confirm_delete_portfolio")
    if st.button("Delete Portfolio", disabled=not confirm_delete, type="primary"):
        delete_portfolio(portfolio_id)
        st.session_state.selected_portfolio = "<none>"
        st.session_state.pop("confirm_delete_portfolio", None)
        st.success(f"Deleted '{selected_name}'.")
        st.rerun()

st.divider()
st.subheader("Holdings")

match = stock_search_and_pick("add_holding", label="Search for a stock to add")
form_cols = st.columns([1, 1, 1], vertical_alignment="bottom")
shares_input = form_cols[0].number_input("Shares", min_value=0.0, step=1.0, key="add_holding_shares")
cost_input = form_cols[1].number_input("Avg cost (₹)", min_value=0.0, step=0.01, key="add_holding_cost")
add_clicked = form_cols[2].button(f"Add {display_symbol(match.symbol)}" if match else "Add Holding", disabled=match is None)
if add_clicked and match is not None:
    if shares_input <= 0:
        st.error("Enter a positive share count before adding this holding.")
    elif cost_input < 0:
        st.error("Average cost can't be negative.")
    elif _ensure_ingested(match.symbol):
        try:
            add_holding(portfolio_id, match.symbol, shares_input, cost_input)
            reset_stock_search("add_holding")
            st.session_state.pop("add_holding_shares", None)
            st.session_state.pop("add_holding_cost", None)
            st.rerun()
        except Exception as exc:
            logger.error("Failed to add holding %s to portfolio %s: %s", match.symbol, portfolio_id, exc)
            st.error(f"Couldn't save this holding: {exc}")

with st.expander("Import holdings from CSV"):
    st.caption("Columns: Symbol, Shares, Avg Cost (Avg Cost is optional; Symbol accepts a company name, bare ticker, or full .NS/.BO symbol).")
    uploaded_csv = st.file_uploader("Choose a CSV file", type="csv", key="portfolio_csv_uploader")
    if uploaded_csv is not None and st.button("Import Holdings"):
        try:
            import_df = pd.read_csv(uploaded_csv)
        except Exception as exc:
            st.error(f"Couldn't read that CSV: {exc}")
            import_df = None
        if import_df is not None:
            cols_lower = {c.lower().strip(): c for c in import_df.columns}
            if not {"symbol", "shares"}.issubset(cols_lower.keys()):
                st.error("CSV must have at least 'Symbol' and 'Shares' columns.")
            else:
                symbol_col, shares_col = cols_lower["symbol"], cols_lower["shares"]
                cost_col = cols_lower.get("avg cost", cols_lower.get("avg_cost"))
                imported, skipped = 0, []
                for _, csv_row in import_df.iterrows():
                    raw_symbol = str(csv_row[symbol_col]).strip()
                    canonical = resolve_symbol(raw_symbol)
                    shares_value = csv_row[shares_col]
                    if not canonical or pd.isna(shares_value) or float(shares_value) <= 0:
                        skipped.append(raw_symbol)
                        continue
                    cost_value = float(csv_row[cost_col]) if cost_col and pd.notna(csv_row.get(cost_col)) else 0.0
                    try:
                        if _ensure_ingested(canonical):
                            add_holding(portfolio_id, canonical, float(shares_value), cost_value)
                            imported += 1
                        else:
                            skipped.append(raw_symbol)
                    except IngestionError:
                        skipped.append(raw_symbol)
                if imported:
                    st.success(f"Imported {imported} holding(s).")
                if skipped:
                    st.warning(f"Skipped {len(skipped)} row(s) that couldn't be resolved: {', '.join(skipped)}")
                if imported:
                    st.rerun()

holdings = list_holdings(portfolio_id)

if not holdings:
    render_empty_state("No holdings yet", "Search for a stock above and add it to this portfolio.", icon="\U0001F4C8")
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
            "Symbol": display_symbol(h["symbol"]),
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
st.download_button(
    "Download Holdings CSV",
    data=display_holdings_df.to_csv(index=False).encode("utf-8"),
    file_name=f"finsight_{selected_name.replace(' ', '_')}_holdings.csv",
    mime="text/csv",
)

edit_col, delete_col = st.columns(2)
# Not a universe search: both pick from this portfolio's own already-loaded holdings
# to act on one, the same "already-owned small set" exception as Market Overview's
# watchlist-removal picker -- not a stock-discovery input.
holding_labels = ["<none>"] + [f"{r['Symbol']} (id {r['id']})" for r in holdings_rows]
holdings_by_label = {f"{r['Symbol']} (id {r['id']})": r for r in holdings_rows}

with edit_col:
    edit_choice = st.selectbox("Edit a holding", options=holding_labels, key="edit_holding_choice")
    if edit_choice != "<none>":
        current_row = holdings_by_label[edit_choice]
        new_shares = st.number_input(
            "New shares", min_value=0.0, step=1.0, value=float(current_row["Shares"]), key="edit_holding_shares"
        )
        new_cost = st.number_input(
            "New avg cost (₹)", min_value=0.0, step=0.01, value=float(current_row["Avg Cost"]), key="edit_holding_cost"
        )
        if st.button("Update Holding"):
            if new_shares <= 0:
                st.error("Enter a positive share count before updating this holding.")
            else:
                try:
                    if update_holding(current_row["id"], new_shares, new_cost):
                        st.success(f"Updated {current_row['Symbol']}.")
                        st.rerun()
                    else:
                        st.error("That holding no longer exists -- it may have just been deleted elsewhere.")
                except Exception as exc:
                    logger.error("Failed to update holding %s: %s", current_row["id"], exc)
                    st.error(f"Couldn't update this holding: {exc}")

with delete_col:
    delete_choice = st.selectbox("Remove a holding", options=holding_labels, key="delete_holding_choice")
    if delete_choice != "<none>" and st.button("Delete Holding"):
        holding_id = int(delete_choice.split("id ")[1].rstrip(")"))
        try:
            delete_holding(holding_id)
            st.rerun()
        except Exception as exc:
            logger.error("Failed to delete holding %s: %s", holding_id, exc)
            st.error(f"Couldn't delete this holding: {exc}")

valid_symbols = [h["symbol"] for h in holdings if h["symbol"] in current_prices]
if not valid_symbols:
    st.warning("No price data available for the current holdings.")
    st.stop()

# Aggregated (summed) per symbol, not a plain {symbol: shares} dict comprehension --
# a symbol held via more than one lot (two separate Add actions) must contribute all
# of its shares to portfolio-level totals, not just the last lot read. See
# core.portfolio.aggregate_shares_by_symbol's docstring for the confirmed bug this fixes.
shares_map = aggregate_shares_by_symbol([h for h in holdings if h["symbol"] in current_prices])
weights = portfolio_weights(shares_map, current_prices)
total_portfolio_value = sum(shares_map[s] * current_prices[s] for s in shares_map)

st.divider()
col_alloc, col_metrics = st.columns([1, 1])

with col_alloc:
    st.subheader("Allocation")
    fig = go.Figure(
        go.Pie(
            labels=[display_symbol(s) for s in weights.keys()],
            values=list(weights.values()),
            marker=dict(colors=theme.CATEGORICAL[: len(weights)]),
            hole=0.4,
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=350)
    st.plotly_chart(fig, use_container_width=True)

price_df = get_multi_symbol_close(valid_symbols)

sharpe = None
drawdown = None
daily_returns = None
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
        metric_cols[0].metric("Sharpe Ratio" if mode == "Professional" else "Reward for the risk", f"{sharpe:.2f}")
        metric_cols[1].metric("Max Drawdown" if mode == "Professional" else "Worst dip from a peak", f"{drawdown:.1%}")
        render_explanation(explain_sharpe(sharpe), mode)
        render_explanation(explain_drawdown(drawdown), mode)

st.divider()
col_sector, col_div, col_risk = st.columns(3)

with col_sector:
    st.subheader("Sector Allocation")
    sector_by_symbol = {s: (_load_info(s) or {}).get("sector") for s in valid_symbols}
    sector_weights = sector_allocation(sector_by_symbol, weights)
    fig = go.Figure(
        go.Pie(
            labels=list(sector_weights.keys()),
            values=list(sector_weights.values()),
            marker=dict(colors=theme.CATEGORICAL[: len(sector_weights)]),
            hole=0.4,
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=300)
    st.plotly_chart(fig, use_container_width=True)

with col_div:
    st.subheader("Diversification")
    div_score = diversification_score(weights)
    st.metric("Diversification Score" if mode == "Professional" else "How spread out?", f"{div_score:.0f}/100")
    render_explanation(explain_diversification(div_score), mode)

with col_risk:
    st.subheader("Risk Meter")
    vol = portfolio_volatility(daily_returns) if daily_returns is not None else None
    band = risk_level(vol) if vol is not None else None
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=(vol * 100) if vol is not None else 0.0,
            number={"suffix": "%"},
            gauge={
                "axis": {"range": [0, 60]},
                "bar": {"color": theme.DARK_INK_PRIMARY},
                "steps": [
                    {"range": [0, 15], "color": theme.STATUS_GOOD},
                    {"range": [15, 30], "color": theme.STATUS_WARNING},
                    {"range": [30, 60], "color": theme.STATUS_CRITICAL},
                ],
            },
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=300)
    st.plotly_chart(fig, use_container_width=True)
    if band is not None:
        render_explanation(explain_risk_level(band, vol), mode)
    else:
        st.caption("Not enough overlapping history to gauge risk yet.")

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
        fig.add_trace(go.Scatter(x=benchmark_cum.index, y=benchmark_cum * 100, name=benchmark_label, line=dict(color=theme.DARK_INK_MUTED, width=2, dash="dot")))
        theme.apply_dark_layout(
            fig,
            yaxis_title="Cumulative Return (%)",
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
    corr_labels = [display_symbol(s) for s in corr.columns]
    fig = go.Figure(
        go.Heatmap(
            z=corr.values,
            x=corr_labels,
            y=corr_labels,
            colorscale=theme.DIVERGING_BLUE_RED,
            zmid=0,
            zmin=-1,
            zmax=1,
            text=corr.round(2).values,
            texttemplate="%{text}",
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=400)
    st.plotly_chart(fig, use_container_width=True)

st.divider()
st.subheader("Monte Carlo Simulation")
if daily_returns is None or daily_returns.empty:
    st.caption("Not enough overlapping history to run a simulation yet.")
else:
    horizon_label = st.radio("Horizon", options=["3 months", "6 months", "1 year"], index=2, horizontal=True)
    horizon_days = {"3 months": 63, "6 months": 126, "1 year": 252}[horizon_label]
    if st.button("Run Monte Carlo Simulation"):
        with st.spinner("Simulating 500 possible paths from this portfolio's own historical daily returns..."):
            paths = monte_carlo_simulation(daily_returns, total_portfolio_value, horizon_days=horizon_days, num_simulations=500)
        st.session_state["mc_paths"] = paths
        st.session_state["mc_portfolio"] = selected_name
        st.session_state["mc_horizon"] = horizon_label

    if st.session_state.get("mc_portfolio") == selected_name and st.session_state.get("mc_horizon") == horizon_label:
        paths = st.session_state.get("mc_paths")
        if paths is not None and not paths.empty:
            percentiles = paths.quantile([0.05, 0.5, 0.95], axis=1).T
            percentiles.columns = ["p5", "p50", "p95"]
            trading_dates = pd.bdate_range(start=pd.Timestamp.today().normalize(), periods=len(percentiles))

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=trading_dates, y=percentiles["p95"], line=dict(width=0), showlegend=False, hoverinfo="skip"))
            fig.add_trace(
                go.Scatter(
                    x=trading_dates,
                    y=percentiles["p5"],
                    fill="tonexty",
                    fillcolor="rgba(42, 120, 214, 0.2)",
                    line=dict(width=0),
                    name="5th-95th percentile range",
                )
            )
            fig.add_trace(go.Scatter(x=trading_dates, y=percentiles["p50"], name="Median simulated path", line=dict(color=theme.CATEGORICAL[0], width=2)))
            theme.apply_dark_layout(
                fig,
                yaxis_title="Simulated Portfolio Value (₹)",
                margin=dict(t=10, l=10, r=10, b=10),
                height=380,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig, use_container_width=True)

            end_p5, end_p50, end_p95 = percentiles.iloc[-1][["p5", "p50", "p95"]]
            mc_cols = st.columns(3)
            mc_cols[0].metric("Pessimistic (5th pct)" if mode == "Professional" else "If things go badly", format_inr(end_p5))
            mc_cols[1].metric("Median" if mode == "Professional" else "Most likely", format_inr(end_p50))
            mc_cols[2].metric("Optimistic (95th pct)" if mode == "Professional" else "If things go well", format_inr(end_p95))
            st.caption(
                f"500 simulated {horizon_label} paths, built by resampling (with replacement) from this "
                "portfolio's own historical daily returns -- not a forecast, prediction, or guarantee. "
                "Past patterns may not repeat; this is a range of statistically plausible outcomes, not advice."
            )

st.divider()
portfolio_ai_data = {
    "portfolio_name": selected_name,
    "num_holdings": len(holdings),
    "holdings": {display_symbol(h["symbol"]): round(weights.get(h["symbol"], 0.0), 3) for h in holdings if h["symbol"] in weights},
    "sharpe_ratio": round(float(sharpe), 2) if sharpe is not None else None,
    "max_drawdown": round(float(drawdown), 3) if drawdown is not None else None,
}
portfolio_fallback_bits = []
if sharpe is not None:
    portfolio_fallback_bits.append((explain_sharpe(sharpe).simple if mode == "Simple" else explain_sharpe(sharpe).professional))
if drawdown is not None:
    portfolio_fallback_bits.append((explain_drawdown(drawdown).simple if mode == "Simple" else explain_drawdown(drawdown).professional))
portfolio_fallback = " ".join(portfolio_fallback_bits) or "Add holdings with enough price history to see a risk summary here."
render_ai_panel(f"Portfolio review for '{selected_name}'", portfolio_ai_data, portfolio_fallback, mode)

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
