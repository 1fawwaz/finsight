"""About: architecture, stack, and disclaimer."""

import streamlit as st

from core.ui_components import render_mode_toggle

st.set_page_config(page_title="FinSight | About", page_icon="\U0001F4C8", layout="wide")
st.title("About FinSight")

render_mode_toggle()

st.markdown(
    """
FinSight is an AI finance and trading intelligence platform for the **Indian market
(NSE/BSE)**, covering market data, technical analysis, portfolio tracking, AI-scored
news sentiment, and a walk-forward-backtested ML direction classifier.

## Architecture

Business logic lives entirely in `core/`; Streamlit pages only orchestrate and render.

| Module | Responsibility |
|---|---|
| `core/database.py` | SQLAlchemy ORM models + session management |
| `core/data_ingestion.py` | yfinance fetch, validation, idempotent DB upsert |
| `core/queries.py` | Read-side DB queries used by pages |
| `core/indicators.py` | SMA, EMA, RSI, MACD, Bollinger Bands, ATR, ADX, VWAP, Support/Resistance, volatility, returns |
| `core/portfolio.py` | Weights, Sharpe ratio, max drawdown, correlation, holdings CRUD |
| `core/sentiment.py` | Gemini news sentiment scoring, with a rule-based fallback |
| `core/ml_model.py` | Feature engineering, RandomForest direction classifier, next-day prediction |
| `core/backtester.py` | Walk-forward backtest: accuracy, confusion matrix, equity curve |
| `core/universe.py` | Full NSE equity list, name/ticker search, symbol resolution |
| `core/watchlist.py` | DB-backed watchlist CRUD (shared across the app, not per-page) |
| `core/explain.py` | Plain-language (Simple Mode) and technical (Professional Mode) explanations |
| `core/ui_components.py` | Shared search/add widget and mode toggle used on every page |
| `core/formatting.py` | Indian Rupee (₹) digit-grouped currency formatting |
| `core/market_status.py` | NSE open/closed status in IST |

## Stack

Python, Streamlit, Plotly, yfinance, pandas/numpy, SQLAlchemy + SQLite (swappable to
PostgreSQL by changing `DATABASE_URL`), scikit-learn, rapidfuzz, Google Gemini (`google-generativeai`).

## Simple Mode vs Professional Mode

Every page has a **Mode** toggle in the sidebar. **Simple Mode** explains every metric,
chart, and AI/ML output in plain language, with no jargon — built for someone with zero
finance background. **Professional Mode** shows the full technical detail (raw indicator
values, statistical terms, confidence figures) for users who want it. Nothing is lost by
switching — both modes read from the same underlying numbers.

## Scope

Indian equities only — NSE (`.NS`) and BSE (`.BO`) tickers, plus the Nifty 50 (`^NSEI`)
and Sensex (`^BSESN`) benchmark indices. All currency is INR (₹).

## Disclaimer

**FinSight is a signal-research and education tool. Nothing in this application is
financial advice.** Technical indicators, AI-scored sentiment, and ML predictions are
shown for research and learning purposes only. Direction-classifier accuracy in the
52-58% range (sometimes lower, as shown honestly on the ML Signals page) is realistic
for daily equity data — barely better than chance — and should never be the sole basis
for a trading decision. Past performance, backtested or otherwise, does not predict
future results. Consult a licensed financial advisor before making investment decisions.
"""
)

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
