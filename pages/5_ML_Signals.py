"""ML Signals: walk-forward-backtested direction classifier with honest accuracy reporting."""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.backtester import walk_forward_backtest
from core.config import DEFAULT_TICKERS, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.explain import explain_ml_prediction
from core.market_status import prediction_target_session
from core.ml_model import make_dataset, predict_next_direction
from core.queries import get_price_history
from core.sentiment import get_stored_sentiment
from core.ui_components import (
    display_symbol,
    render_ai_panel,
    render_explanation,
    render_mode_toggle,
    render_prediction_disclaimer,
    stock_picker,
)

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | ML Signals", page_icon="\U0001F4C8", layout="wide")
st.title("ML Signals")

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


@st.cache_data(ttl=900, show_spinner=False)
def _predict_next(symbol: str):
    history = _load_history(symbol)
    sentiment_series = _load_sentiment_series(symbol)
    return predict_next_direction(history, sentiment_by_date=sentiment_series)


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
next_prediction = _predict_next(symbol)
if next_prediction is None:
    st.caption("Not enough history yet to make a prediction for this ticker.")
else:
    predicted_up, probability_up = next_prediction
    has_backtest = st.session_state.get("ml_symbol") == symbol and "ml_result" in st.session_state
    historical_accuracy = st.session_state["ml_result"].accuracy if has_backtest else 0.55
    pred_cols = st.columns([1, 3])
    pred_cols[0].metric(
        "Direction" if mode == "Professional" else "Guess",
        "⬆ Up" if predicted_up else "⬇ Down",
        f"{probability_up:.0%} confidence" if mode == "Professional" else None,
    )
    with pred_cols[1]:
        render_explanation(
            explain_ml_prediction(predicted_up, probability_up, historical_accuracy, target_session_label),
            mode,
        )
        if not has_backtest:
            st.caption(
                "Run the backtest below to see exactly how often this model has been right for "
                f"{display_symbol(symbol)} historically -- the number above uses a general baseline until then."
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
    ml_ai_data["next_day_probability_up"] = round(float(next_prediction[1]), 3)
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
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
