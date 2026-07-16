"""AI Dashboard: one-page enterprise view of a symbol's full Explainable-AI state --
prediction, confidence, risk, feature importance, historical accuracy, prediction
timeline, model/dataset lineage, drift status, and system health. Aggregates existing
core.ml.* modules only (the same single pipeline every other page uses) -- no new
prediction/risk/explanation logic of its own, per the project's "one prediction
pipeline, one explanation engine" rule.
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import theme
from core.config import DEFAULT_TICKERS, get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.design import inject_design_system
from core.ml.drift import assess_drift
from core.ml.performance import prediction_timeline
from core.ml.prediction_service import generate_prediction
from core.ml.registry import list_registry_entries
from core.ml.system_health import run_all_checks
from core.ml_model import REGISTRY_MODEL_NAME
from core.queries import get_price_history
from core.sentiment import get_stored_sentiment
from core.ui_components import (
    display_symbol,
    render_mode_toggle,
    render_page_header,
    render_prediction_disclaimer,
    stock_picker,
)

logger = get_logger(__name__)

st.set_page_config(page_title="FinSight | AI Dashboard", page_icon="\U0001F9E0", layout="wide")
inject_design_system()
render_page_header(
    "AI Dashboard",
    "One-page view of the AI system's current state for a symbol -- prediction, confidence, risk, drift, and system health.",
)

mode = render_mode_toggle()
render_prediction_disclaimer()


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


symbol = stock_picker("ai_dashboard_symbol", default_symbol=DEFAULT_TICKERS[0])

if _load_history(symbol).empty:
    with st.spinner(f"Fetching {display_symbol(symbol)} from Yahoo Finance..."):
        try:
            ingest_ticker(symbol)
            _load_history.clear()
        except IngestionError as exc:
            st.warning(f"Couldn't fetch {display_symbol(symbol)}: {exc}")
            st.stop()

history = _load_history(symbol)
sentiment_series = _load_sentiment_series(symbol)
# Not st.cache_data-wrapped, same reasoning as pages/5_ML_Signals.py's _predict_next:
# PredictionResult isn't plain-hashable, and registry inference is already cheap. This
# also means the dashboard always reflects the current model/registry/DB state on every
# rerun -- no separate "auto-refresh" timer is layered on top (see AI_ARCHITECTURE
# note in AI_DASHBOARD_REPORT.md for why a periodic st.fragment(run_every=...) here
# would repeatedly re-trigger the expensive SHAP/PSI computations below).
result = generate_prediction(symbol, history, sentiment_by_date=sentiment_series)

if not result.has_prediction:
    st.warning("Not enough data to generate a prediction for this symbol.")
    for w in result.warnings:
        st.caption(f"⚠️ {w}")
    st.stop()

confidence = result.confidence

st.divider()
st.subheader("Latest Prediction")
top_cols = st.columns(4)
top_cols[0].metric("Direction", "⬆ Up" if confidence.prediction_class == "UP" else "⬇ Down")
top_cols[1].metric("Confidence", confidence.confidence_level, f"{confidence.confidence_score:.0f}/100")
top_cols[2].metric("Risk", result.risk.risk_level if result.risk is not None else "Unavailable", f"{result.risk.risk_score:.0f}/100" if result.risk is not None else None)
top_cols[3].metric("Data Freshness", result.data_freshness or "Unknown")

gauge_cols = st.columns(2)
with gauge_cols[0]:
    st.caption("Confidence Gauge")
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=confidence.confidence_score,
            number={"suffix": "/100"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": theme.DARK_INK_PRIMARY},
                "steps": [
                    {"range": [0, 15], "color": theme.STATUS_CRITICAL},
                    {"range": [15, 35], "color": theme.STATUS_SERIOUS},
                    {"range": [35, 60], "color": theme.STATUS_WARNING},
                    {"range": [60, 100], "color": theme.STATUS_GOOD},
                ],
            },
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=260)
    st.plotly_chart(fig, use_container_width=True)

with gauge_cols[1]:
    st.caption("Risk Gauge")
    if result.risk is not None:
        fig = go.Figure(
            go.Indicator(
                mode="gauge+number",
                value=result.risk.risk_score,
                number={"suffix": "/100"},
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": theme.DARK_INK_PRIMARY},
                    "steps": [
                        {"range": [0, 25], "color": theme.STATUS_GOOD},
                        {"range": [25, 50], "color": theme.STATUS_WARNING},
                        {"range": [50, 75], "color": theme.STATUS_SERIOUS},
                        {"range": [75, 100], "color": theme.STATUS_CRITICAL},
                    ],
                },
            )
        )
        theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=260)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.caption("No risk assessment available for this prediction.")

st.subheader("Probability Distribution")
fig = go.Figure(
    go.Bar(
        x=["Up", "Down"],
        y=[confidence.probability_up, confidence.probability_down],
        marker_color=[theme.STATUS_GOOD, theme.STATUS_CRITICAL],
        text=[f"{confidence.probability_up:.0%}", f"{confidence.probability_down:.0%}"],
        textposition="auto",
    )
)
theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=260, yaxis_range=[0, 1])
st.plotly_chart(fig, use_container_width=True)

st.subheader("Feature Importance")
if result.explanation is not None:
    ranked = result.explanation.feature_importance_ranking[:10]
    fig = go.Figure(
        go.Bar(
            x=[v for _, v in ranked][::-1],
            y=[n for n, _ in ranked][::-1],
            orientation="h",
            marker_color=theme.SEQUENTIAL_BLUE[2],
        )
    )
    theme.apply_dark_layout(fig, margin=dict(t=10, l=10, r=10, b=10), height=350, xaxis_title="|SHAP contribution|")
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("No per-prediction explanation is available for this model (SHAP requires the registry tree-model path).")

st.subheader("Historical Accuracy")
perf = result.historical_performance
if perf is not None and perf.n > 0:
    perf_cols = st.columns(4)
    perf_cols[0].metric("Accuracy", f"{perf.accuracy:.1%}")
    perf_cols[1].metric("Precision", f"{perf.precision:.1%}")
    perf_cols[2].metric("Recall", f"{perf.recall:.1%}")
    perf_cols[3].metric("Resolved Predictions", perf.n)
else:
    st.caption("No resolved live predictions yet for this symbol/model.")

st.subheader("Prediction Timeline")
timeline = prediction_timeline(symbol, model_version=result.model_version, limit=60)
if timeline:
    tl_df = pd.DataFrame(timeline).sort_values("date")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=tl_df["date"], y=tl_df["probability"], mode="lines+markers", name="Predicted P(up)",
            line=dict(color=theme.CATEGORICAL[0]),
        )
    )
    resolved = tl_df["actual_direction"].notna()
    correct = resolved & (tl_df["actual_direction"] == tl_df["predicted_direction"])
    incorrect = resolved & (tl_df["actual_direction"] != tl_df["predicted_direction"])
    if correct.any():
        fig.add_trace(
            go.Scatter(
                x=tl_df.loc[correct, "date"], y=tl_df.loc[correct, "probability"], mode="markers", name="Correct",
                marker=dict(color=theme.STATUS_GOOD, size=11, symbol="circle"),
            )
        )
    if incorrect.any():
        fig.add_trace(
            go.Scatter(
                x=tl_df.loc[incorrect, "date"], y=tl_df.loc[incorrect, "probability"], mode="markers", name="Incorrect",
                marker=dict(color=theme.STATUS_CRITICAL, size=11, symbol="x"),
            )
        )
    theme.apply_dark_layout(
        fig, margin=dict(t=10, l=10, r=10, b=10), height=300, yaxis_title="Predicted P(up)", yaxis_range=[0, 1],
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.caption("No prediction history recorded yet for this symbol/model -- visit ML Signals to generate and record one.")

st.divider()
st.subheader("Model & Dataset Lineage")
lineage_cols = st.columns(4)
lineage_cols[0].metric("Model", result.model_version or "in-app fallback")
lineage_cols[1].metric("Model Status", result.model_status or "N/A")
lineage_cols[2].metric("Dataset", result.dataset_version or "N/A")
lineage_cols[3].metric("Dataset Rows", f"{result.dataset_size:,}" if result.dataset_size is not None else "N/A")

st.subheader("Drift Status")
if result.model_source == "registry":
    registered_accuracy = None
    match = next((e for e in list_registry_entries(result.model_name) if e["version"] == result.model_version), None)
    if match is not None:
        registered_accuracy = match["metrics"].get("accuracy")
    with st.spinner("Computing drift report (loads the training feature set on first use)..."):
        drift_report = assess_drift(
            symbol, result.model_version, result.feature_version, history,
            registered_accuracy=registered_accuracy, sentiment_by_date=sentiment_series,
        )
    drift_cols = st.columns(3)
    drift_cols[0].metric("Feature/Data Drift", drift_report.data_drift_status)
    drift_cols[1].metric("Prediction Drift", drift_report.prediction_drift_status)
    drift_cols[2].metric("Concept Drift", drift_report.concept_drift_status)
    if drift_report.recommend_retraining:
        st.warning("Significant drift detected -- consider retraining this model.")
    for w in drift_report.warnings:
        st.caption(f"⚠️ {w}")
else:
    st.caption("Drift assessment requires a registered model; this prediction used the in-app fallback.")

st.divider()
st.subheader("System Health")
health_checks = run_all_checks(REGISTRY_MODEL_NAME, history, result)
for check in health_checks:
    icon = "\U0001F7E2" if check.ok else "\U0001F534"
    st.caption(f"{icon} {check.name}: {check.detail}")

st.divider()
st.caption("FinSight is a signal-research and education tool. Nothing shown here is financial advice.")
