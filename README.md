# FinSight

AI Finance & Trading Intelligence Platform for the Indian market (NSE/BSE) — a
Streamlit app that ingests real market data, computes professional-grade technical
analytics, scores news sentiment with an LLM, runs a walk-forward-backtested ML
direction classifier, and explains all of it in either plain language or full
technical depth.

> **FinSight is a signal-research and education tool. Nothing here is financial advice.**

## Features

- **Universal search, everywhere** — one shared search box (company name, ticker, or
  partial text) works identically across every page. The full NSE equity universe
  (2,300+ listings) is searchable; users never type a `.NS`/`.BO` suffix.
- **Simple / Professional mode** — a sidebar toggle switches every metric, chart label,
  and AI explanation between kid-friendly plain language (no jargon, everyday analogies)
  and full technical depth (RSI, MACD, ATR, ADX, VWAP, Sharpe ratio, etc.). Both modes
  read from the same underlying numbers.
- **NSE holiday calendar** — the app always knows the current IST session (pre-open,
  open, post-close, closed), today's holiday if it's one, and the next/previous trading
  day. Predictions and copy say "next trading session" with the real date, never a
  hardcoded "tomorrow" that could land on a weekend or exchange holiday.
- **Market Overview** — a DB-persisted watchlist (shared across the whole app, not
  per-session) with live price/RSI, sector/name filters, CSV export, 52-week range,
  sector heatmap, top movers, volume leaders, and NSE market-hours status (IST).
- **Stock Analysis** — candlestick charts with SMA/EMA/Bollinger/VWAP/Support-Resistance
  overlays, RSI + MACD subplots, and an AI-narrated analysis panel.
- **Portfolio** — CRUD holdings (via the same universal search, plus CSV import/export),
  allocation pie, sector allocation, a diversification score, a risk meter, cumulative
  return vs Nifty 50/Sensex, Sharpe ratio, max drawdown, correlation matrix, and a
  bootstrap Monte Carlo simulation (5th-95th percentile fan chart) of future portfolio value.
- **AI Sentiment** — Gemini-scored news sentiment per ticker (real SQLite UPSERT, no
  duplicate writes), with a rule-based keyword fallback when no API key is configured.
- **ML Signals** — a genuine next-trading-session "Guess" prediction, calendar-aware
  (not just historical backtest numbers), plus a walk-forward-backtested classifier with
  honestly reported accuracy/precision/recall, confusion matrix, and equity curve vs
  buy-and-hold. The live prediction prefers a registered model from the Phase 3
  production ML pipeline (`core/ml/`, below) when one exists, falling back to the
  original in-app RandomForest otherwise -- see "Production ML Pipeline" below.
- **Ask FinSight AI** — a real analyst pipeline, not a chatbot glued to an LLM: intent
  detection routes each question (single stock, comparison, portfolio review, market
  overview, indicator explainer, sector query) through calendar-aware live price,
  technical, fundamental, news-sentiment, ML-prediction, and portfolio context before
  Gemini ever sees it. Conversation memory resolves short follow-ups ("What about
  Infosys?", "Which one is safer?") without repeating context. Every response carries
  the real current IST date/time/session status and the actual next-trading-session
  date — never a hardcoded "tomorrow". The rule-based fallback is fully structured
  (not a one-line data dump), so answers are never generic even without Gemini.
- **AI explanation panels** — every analytical page has a "What the AI Thinks" panel:
  Gemini synthesizes that page's own computed numbers into a short narrative, with a
  rule-based fallback that's never blank.
- **Premium dark theme** — a real dark theme (not default Streamlit light mode) applied
  consistently across every page and chart.
- **About** — architecture and disclaimer.

Scope is India-only: NSE (`.NS`) and BSE (`.BO`) tickers, INR (₹) currency throughout,
Nifty 50 / Sensex / Bank Nifty as market context.

## Architecture

Business logic lives in `core/`; Streamlit pages (`app.py`, `pages/`) only orchestrate
and render — no calculations happen inside a page.

```
app.py                    Home dashboard: market status, portfolio value, watchlist,
                           AI market summary, recent searches, quick search
pages/
  1_Market_Overview.py
  2_Stock_Analysis.py
  3_Portfolio.py
  4_AI_Sentiment.py
  5_ML_Signals.py
  6_About.py
  7_Ask_FinSight_AI.py
core/
  config.py               Settings, logging, default watchlist, market validation
  database.py             SQLAlchemy ORM models + session management
  data_ingestion.py       yfinance fetch + idempotent DB upsert
  queries.py              Read-side DB queries
  universe.py             Full NSE equity list, name/ticker search, symbol resolution
  watchlist.py            DB-backed watchlist CRUD (shared across the app)
  ui_components.py        Shared search/add widget, mode toggle, AI panel renderer
  indicators.py           SMA, EMA, RSI, MACD, Bollinger, ATR, ADX, VWAP,
                           Support/Resistance, volatility, returns
  portfolio.py            Weights, Sharpe, max drawdown, correlation, holdings CRUD
  sentiment.py            Gemini sentiment scoring + rule-based fallback
  ml_model.py             Feature engineering + RandomForest classifier + next-day
                           prediction; prefers a registered Phase 3 model when active
  ml/                      Production ML pipeline (see below)
  backtester.py           Walk-forward backtest
  explain.py              Plain-language (Simple) and technical (Professional) explanations
  ai_explain.py           Gemini-narrated "AI Analysis" panel, per-page, with fallback
  market_summary.py       AI-narrated home-dashboard market summary, with fallback
  fundamentals.py         Cached P/E, dividend rate, market cap, 52-week range (yfinance)
  chat.py                 "Ask FinSight AI": intent detection, conversation memory,
                           calendar-aware grounded Q&A, structured fallback
  formatting.py           Indian Rupee (₹) digit-grouped formatting
  market_status.py        NSE session status, holiday calendar, next/previous trading day (IST)
  theme.py                Shared Plotly color constants + dark chart layout helper
tests/                    pytest suite (91%+ coverage on core/)
data/                     SQLite DB (gitignored)
.streamlit/config.toml    Dark theme + error-detail suppression
```

The database is SQLite by default (`data/finsight.db`); swapping to PostgreSQL only
requires changing `DATABASE_URL`.

## Production ML Pipeline (`core/ml/`)

A versioned, reproducible pipeline for the next-trading-session direction classifier,
built on top of (not duplicating) the existing price-ingestion and feature code:

```
core/ml/
  data_layer.py           Dataset versioning (ml_dataset_versions) + quality validation
                           (schema/range/duplicate/outlier checks) on top of the
                           existing incremental yfinance ingestion, with retries
  feature_pipeline.py     27-feature extended set (core.ml_model's 9 + 18 more reusing
                           core.indicators: ATR, ADX, VWAP, Bollinger, SMA/EMA distance,
                           ROC, momentum, volume ratio, gap %, candle anatomy,
                           support/resistance and 52-week-range distance) + the SQLite
                           feature store (ml_feature_sets / ml_feature_values)
  cv.py                   Chronological train/val/test split + walk-forward CV by
                           unique date, with an enforced no-leakage assertion per fold
  baseline.py              Naive persistence baseline every model is measured against
  training.py              Optuna-tuned training across CatBoost/XGBoost/LightGBM/
                           RandomForest; every trial logged to ml_training_runs
  generalization.py       The mandatory overfit/underfit/fold-instability gate +
                           per-feature leakage audit
  corrective_actions.py   Regularization / feature-selection / validation-targeted
                           re-tune strategies for a model flagged by the gate
  ensemble.py              Soft-voting ensemble (tried in the improvement loop)
  registry.py              Model artifact + full-lineage persistence (ml_model_registry)
  evaluation.py            Confusion matrix, learning curves, feature importance, SHAP
                           -- generated and persisted (JSON + PNG) under data/ml_evaluation/
  improvement_loop.py      Keep/revert decision rule + iteration logging
                           (ml_improvement_iterations)
```

The currently active model is `finsight_direction_classifier_v1` (XGBoost): on the
held-out, chronologically-final test fold it scores ROC-AUC 0.515 vs the naive baseline's
~0.50 and the prior in-app RandomForest's 0.491 on the identical fold -- a real, if
modest, improvement. It does **not** beat the naive baseline on raw accuracy at the
default 0.5 threshold (47.6% vs 49.2%), which is disclosed rather than hidden: daily
NSE-equity direction is close to a random walk at this granularity, consistent with
published research. See `docs/phase3_evidence/` for the full training/gate/
improvement-loop evidence behind this number.

## Setup

Requires Python 3.12+ (the Phase 3 ML dependency `xgboost==3.3.0` requires it).

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Add your GEMINI_API_KEY to .env, or leave it blank to use rule-based fallbacks
# throughout (sentiment scoring, AI panels, market summary, and chat all degrade
# gracefully to non-Gemini fallbacks -- nothing is ever blank)

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

91%+ coverage on `core/` (346 tests), including a dedicated lookahead-bias regression
test for the ML feature pipeline, race-condition tests proving the news-sentiment and
Ticker-creation UPSERTs are actually atomic, and regression tests for universal-search
false positives (e.g. a bare 5-6 character foreign ticker guess silently resolving to
an unrelated NSE company by coincidental fuzzy-match score).

## Disclaimer

FinSight is a signal-research and education tool, not a financial advisor. Technical
indicators, AI-scored sentiment, and ML predictions are for research and learning only.
Direction-classifier accuracy in the 52–58% range (sometimes lower, as shown honestly
on the ML Signals page) is realistic for daily equity data — barely better than chance
— and should never be the sole basis for a trading decision. Past performance,
backtested or otherwise, does not predict future results. Consult a licensed financial
advisor before making investment decisions.
