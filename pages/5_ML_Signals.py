"""ML Signals: walk-forward-backtested direction classifier with honest accuracy reporting."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.backtester import walk_forward_backtest
from core.config import DEFAULT_TICKERS, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.design import inject_design_system
from core.explain import explain_ml_prediction
from core.market_status import prediction_target_session
from core.ml.performance import performance_by_confidence_bucket
from core.ml.prediction_service import generate_prediction
from core.ml.prediction_tracking import record_prediction, resolve_pending_outcomes
from core.ml_model import make_dataset
from core.queries import get_price_history
from core.sentiment import get_stored_sentiment
from core.ui_components import (
    display_symbol,
    render_ai_panel,
    render_explanation,
    render_mode_toggle,
    render_page_header,
    render_prediction_disclaimer,
    render_prediction_result,
    stock_picker,
)

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | ML Signals", page_icon="\U0001F4C8", layout="wide")
inject_design_system()
render_page_header("ML Signals", "Walk-forward-backtested direction classifier with honest accuracy reporting.")

mode = render_mode_toggle()

render_prediction_disclaimer()
if mode == "Professional":
    st.caption(
        "A realistic direction classifier on daily equity data typically lands around "
        "**52-58% accuracy** — barely better than a coin flip. Treat every number on this "
        "page as a research signal, not a recommendation to trade."
    )


@st.cache_data(ttl=900, show_spinner=False)
def _load_history(symbol: str) -> pd.DataFrame:
    return get_price_history(symbol)


@st.cache_data(ttl=900, show_spinner=False)
def _load_sentiment_series(symbol: str) -> pd.Series | None:
    rows = get_stored_sentiment(symbol)
    if not rows:
        return None
    sent_df = pd.DataFrame(rows)
    sent_df["date"] = pd.to_datetime(sent_df["date"])
    return sent_df.groupby("date")["sentiment"].mean()


@st.cache_data(ttl=3600, show_spinner=False)
def _run_backtest(symbol: str, train_window: int, test_window: int):
    history = _load_history(symbol)
    sentiment_series = _load_sentiment_series(symbol)
    features, labels = make_dataset(history, sentiment_by_date=sentiment_series)
    return walk_forward_backtest(features, labels, history["close"], train_window=train_window, test_window=test_window)


def _predict_next(symbol: str):
    """Not `st.cache_data`-wrapped like the other loaders here: `PredictionResult` isn't
    a plain hashable/serializable value (it holds nested dataclasses added across later
    Explainable-AI phases), and the underlying `predict_next_direction` call it wraps is
    already cheap (registry inference is ~28ms) -- caching would add complexity for
    negligible benefit."""
    history = _load_history(symbol)
    sentiment_series = _load_sentiment_series(symbol)
    return generate_prediction(symbol, history, sentiment_by_date=sentiment_series)


symbol = stock_picker("ml_signals_symbol", default_symbol=DEFAULT_TICKERS[0])

if _load_history(symbol).empty:
    with st.spinner(f"Fetching {display_symbol(symbol)} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {display_symbol(symbol)}: {exc}")
            st.stop()

history = _load_history(symbol)
years_available = (history.index[-1] - history.index[0]).days / 365.25
if years_available < 1.5:
    st.warning(f"Only {years_available:.1f} years of history for {display_symbol(symbol)} — results below may be unreliable.")

target_session = prediction_target_session()
target_session_label = target_session.strftime("%A, %d %b")

st.divider()
st.subheader("Next Trading Session's Guess" if mode == "Simple" else "Next-Session Prediction")
st.caption(f"Predicting for the next trading session: **{target_session_label}**.")
prediction_result = _predict_next(symbol)
render_prediction_result(prediction_result, mode, price_df=history, sentiment_by_date=_load_sentiment_series(symbol))

# Historical Intelligence (Phase 5): record this prediction for later outcome
# resolution, and resolve any of this symbol's earlier predictions whose target
# session has since actually happened. Both are best-effort, deliberately separate
# from generate_prediction (a pure/read function) -- a DB write must never be a hidden
# side effect of computing a result.
if prediction_result.has_prediction:
    try:
        record_prediction(symbol, target_session, prediction_result)
        resolve_pending_outcomes(symbol)
    except Exception as exc:
        logger.warning("Prediction tracking failed for %s: %s", symbol, exc)
next_prediction = (
    (prediction_result.confidence.prediction_class == "UP", prediction_result.confidence.probability_up)
    if prediction_result.has_prediction
    else None
)
has_backtest = st.session_state.get("ml_symbol") == symbol and "ml_result" in st.session_state
if prediction_result.has_prediction:
    if not has_backtest:
        st.caption(
            "Run the backtest below to see exactly how often this model has been right for "
            f"{display_symbol(symbol)} historically."
        )
    else:
        render_explanation(
            explain_ml_prediction(
                next_prediction[0], next_prediction[1], st.session_state["ml_result"].accuracy, target_session_label,
                include_probability=False,
            ),
            mode,
        )

if st.button("Run Walk-Forward Backtest"):
    with st.spinner(f"Training and backtesting on {display_symbol(symbol)} (this walks forward year by year, so it takes a bit)..."):
        try:
            result = _run_backtest(symbol, train_window=252, test_window=21)
            st.session_state["ml_result"] = result
            st.session_state["ml_symbol"] = symbol
        except ValueError as exc:
            st.warning(str(exc))
            st.stop()

if st.session_state.get("ml_symbol") != symbol:
    st.caption("Click **Run Walk-Forward Backtest** to train and evaluate a model for this ticker.")
    st.stop()

result = st.session_state["ml_result"]

st.divider()
st.subheader("Honest Performance")
metric_cols = st.columns(4)
metric_cols[0].metric("Accuracy" if mode == "Professional" else "Right guesses", f"{result.accuracy:.1%}")
metric_cols[1].metric("Precision" if mode == "Professional" else "Right when it said 'up'", f"{result.precision:.1%}")
metric_cols[2].metric("Recall" if mode == "Professional" else "Caught how many real 'up' days", f"{result.recall:.1%}")
metric_cols[3].metric("Out-of-sample days", f"{len(result.predictions):,}")
if mode == "Simple":
    st.caption(
        f"Out of every 10 guesses this computer made in the past, about "
        f"{round(result.accuracy * 10)} were right. That's only a little better than flipping "
        "a coin -- so treat it as a hint, not a promise."
    )

col_confusion, col_equity = st.columns(2)

with col_confusion:
    st.subheader("Confusion Matrix")
    fig = go.Figure(
        go.Heatmap(
            z=result.confusion,
            x=["Predicted Down", "Predicted Up"],
            y=["Actual Down", "Actual Up"],
            colorscale=theme.SEQUENTIAL_BLUE,
            text=result.confusion,
            texttemplate="%{text}",
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=350, yaxis_autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)

with col_equity:
    st.subheader("Equity Curve: Signal vs Buy & Hold")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=result.equity_signal.index,
            y=result.equity_signal,
            name="Signal-following",
            line=dict(color=theme.CATEGORICAL[0], width=2),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=result.equity_buy_hold.index,
            y=result.equity_buy_hold,
            name="Buy & Hold",
            line=dict(color=theme.DARK_INK_MUTED, width=2, dash="dot"),
        )
    )
    theme.apply_dark_layout(
        fig,
        yaxis_title="Growth of ₹1",
        margin=dict(t=10, l=10, r=10, b=10),
        height=350,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)

st.divider()
ml_ai_data = {
    "symbol": display_symbol(symbol),
    "accuracy": round(float(result.accuracy), 3),
    "precision": round(float(result.precision), 3),
    "recall": round(float(result.recall), 3),
    "out_of_sample_days": len(result.predictions),
}
if next_prediction is not None:
    ml_ai_data["next_day_predicted_direction"] = "up" if next_prediction[0] else "down"
ml_fallback = (
    f"Out of every 10 guesses this computer made in the past, about {round(result.accuracy * 10)} were right. "
    "That's only a little better than flipping a coin -- so treat it as a hint, not a promise."
    if mode == "Simple"
    else f"Walk-forward accuracy {result.accuracy:.1%}, precision {result.precision:.1%}, recall {result.recall:.1%} "
    f"over {len(result.predictions)} out-of-sample days -- consistent with the ~52-58% ceiling typical of daily "
    "equity direction classifiers."
)
render_ai_panel(f"ML direction-classifier results for {display_symbol(symbol)}", ml_ai_data, ml_fallback, mode)

st.divider()
st.subheader("Live AI Track Record" if mode == "Simple" else "Historical Intelligence (Live Predictions)")
st.caption(
    "Unlike the walk-forward backtest above (which retrains and tests on historical data), this tracks the "
    f"actual live predictions {display_symbol(symbol)} has shown on this page over time, resolved against what "
    "really happened."
)
live_perf = prediction_result.historical_performance
if live_perf is not None and live_perf.n > 0:
    live_cols = st.columns(4)
    live_cols[0].metric("Accuracy" if mode == "Professional" else "Right guesses", f"{live_perf.accuracy:.1%}")
    live_cols[1].metric("Precision" if mode == "Professional" else "Right when it said 'up'", f"{live_perf.precision:.1%}")
    live_cols[2].metric("Recall" if mode == "Professional" else "Caught real 'up' days", f"{live_perf.recall:.1%}")
    live_cols[3].metric("Resolved live predictions", f"{live_perf.n}")
    if mode == "Professional":
        by_bucket = performance_by_confidence_bucket(symbol=symbol, model_version=prediction_result.model_version)
        if by_bucket:
            st.caption("Accuracy by confidence bucket:")
            bucket_cols = st.columns(len(by_bucket))
            for col, (level, stats) in zip(bucket_cols, sorted(by_bucket.items())):
                col.metric(level, f"{stats.accuracy:.0%}", f"n={stats.n}")
else:
    st.caption(
        "No resolved live predictions yet for this symbol -- come back after this page has made a prediction and "
        "the target trading session has passed, so it can be checked against what actually happened."
    )

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
