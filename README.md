# FinSight

AI Finance & Trading Intelligence Platform for the Indian market (NSE/BSE) — a
Streamlit app that ingests real market data, computes professional-grade technical
analytics, scores news sentiment with an LLM, and runs a walk-forward-backtested ML
direction classifier.

> **FinSight is a signal-research and education tool. Nothing here is financial advice.**

## Features

- **Market Overview** — watchlist with live price/RSI, sector heatmap, top movers, NSE market-hours status (IST)
- **Stock Analysis** — candlestick charts with SMA/EMA/Bollinger overlays, RSI + MACD subplots
- **Portfolio** — CRUD holdings, allocation pie, cumulative return vs Nifty 50/Sensex, Sharpe ratio, max drawdown, correlation matrix
- **AI Sentiment** — Gemini-scored news sentiment per ticker, with a rule-based keyword fallback when no API key is configured
- **ML Signals** — walk-forward-backtested RandomForest direction classifier with honestly reported accuracy/precision/recall, confusion matrix, and equity curve vs buy-and-hold
- **About** — architecture and disclaimer

Scope is India-only: NSE (`.NS`) and BSE (`.BO`) tickers, INR (₹) currency throughout,
Nifty 50 / Sensex as benchmarks.

## Screenshots

_TODO: add screenshots of Market Overview, Stock Analysis, Portfolio, AI Sentiment, and ML Signals._

## Architecture

Business logic lives in `core/`; Streamlit pages (`app.py`, `pages/`) only orchestrate
and render — no calculations happen inside a page.

```
app.py                    Streamlit entrypoint + navigation
pages/
  1_Market_Overview.py
  2_Stock_Analysis.py
  3_Portfolio.py
  4_AI_Sentiment.py
  5_ML_Signals.py
  6_About.py
core/
  config.py               Settings, logging, default watchlist, market validation
  database.py             SQLAlchemy ORM models + session management
  data_ingestion.py       yfinance fetch + idempotent DB upsert
  queries.py              Read-side DB queries
  indicators.py           SMA, EMA, RSI, MACD, Bollinger, volatility, returns
  portfolio.py            Weights, Sharpe, max drawdown, correlation, holdings CRUD
  sentiment.py            Gemini sentiment scoring + rule-based fallback
  ml_model.py             Feature engineering + RandomForest classifier
  backtester.py           Walk-forward backtest
  formatting.py           Indian Rupee (₹) digit-grouped formatting
  market_status.py        NSE open/closed status in IST
  theme.py                Shared Plotly color constants
tests/                    pytest suite (87%+ coverage on core/)
data/                     SQLite DB (gitignored)
```

The database is SQLite by default (`data/finsight.db`); swapping to PostgreSQL only
requires changing `DATABASE_URL`.

## Setup

Requires Python 3.11+.

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Add your GEMINI_API_KEY to .env, or leave it blank to use the rule-based sentiment fallback

python -m core.data_ingestion   # seeds the default 10-ticker watchlist with 5y of history
streamlit run app.py
```

Open http://localhost:8501.

### Docker

```bash
docker build -t finsight .
docker run -p 8501:8501 --env-file .env -v $(pwd)/data:/app/data finsight
```

## Testing

```bash
pytest --cov=core --cov-report=term-missing
```

87%+ coverage on `core/`, including a dedicated lookahead-bias regression test for the
ML feature pipeline.

## Disclaimer

FinSight is a signal-research and education tool, not a financial advisor. Technical
indicators, AI-scored sentiment, and ML predictions are for research and learning only.
Direction-classifier accuracy in the 52–58% range (sometimes lower, as reported
honestly on the ML Signals page) is realistic for daily equity data — barely better
than chance — and should never be the sole basis for a trading decision. Past
performance, backtested or otherwise, does not predict future results. Consult a
licensed financial advisor before making investment decisions.
