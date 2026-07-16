"""Explainable-AI platform phase, Phase 8: runtime drift detection.

Distinct from the pre-existing `core.ml.feature_importance_monitoring` (which compares
feature *importance* across separate training experiments -- a training-time,
offline concern) -- this module compares **live serving-time data and outcomes**
against what the currently-deployed model was actually trained/evaluated on:

- **Feature/data drift**: does a symbol's current feature distribution still resemble
  what the model learned from (Population Stability Index, the standard production-ML
  drift statistic), computed via the exact same feature pipeline
  (`core.ml.feature_pipeline.build_features_v2`) and the same chronological train split
  (`core.ml.cv.chronological_train_val_test_split`) already used elsewhere -- never a
  second feature-engineering or splitting implementation.
- **Prediction drift**: has the model's own output distribution shifted over its own
  recent live history (`core.database.Prediction` rows, Phase 5's tracking table).
- **Concept drift**: has live accuracy (`core.ml.performance`, Phase 5) fallen
  meaningfully short of the accuracy the model was registered with (Phase 6's registry).

Every check reports "Insufficient Data" rather than a fabricated "Stable" when there
isn't yet enough live history to judge -- this is expected and honest for a freshly
deployed model or a rarely-visited symbol, not a bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import select

from core.config import get_logger
from core.database import Prediction, Ticker, get_session

logger = get_logger(__name__)

DRIFT_STATUSES = ("Stable", "Drifting", "Significant Drift", "Insufficient Data")

# Population Stability Index bands -- industry-standard thresholds (< 0.1 stable,
# 0.1-0.25 moderate shift worth watching, > 0.25 a distribution has genuinely moved).
PSI_DRIFTING_THRESHOLD = 0.10
PSI_SIGNIFICANT_THRESHOLD = 0.25

# Minimum live sample sizes below which a check honestly reports Insufficient Data
# rather than a statistically meaningless verdict from a handful of predictions.
MIN_PREDICTIONS_FOR_PREDICTION_DRIFT = 10
MIN_RESOLVED_FOR_CONCEPT_DRIFT = 20


@dataclass
class FeatureDriftResult:
    feature_name: str
    psi: float
    status: str  # Stable / Drifting / Significant Drift


@dataclass
class DriftReport:
    feature_drift: list[FeatureDriftResult] = field(default_factory=list)
    data_drift_status: str = "Insufficient Data"
    prediction_drift_status: str = "Insufficient Data"
    prediction_drift_detail: str | None = None
    concept_drift_status: str = "Insufficient Data"
    concept_drift_detail: str | None = None
    overall_status: str = "Insufficient Data"
    recommend_retraining: bool = False
    warnings: list[str] = field(default_factory=list)


def _psi(train_values: np.ndarray, live_values: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index: bins the *training* distribution into `bins` equal-
    frequency deciles, then compares what share of the live sample falls in each bin.
    PSI=0 means identical distributions; larger values mean more drift. A small epsilon
    keeps a zero-count bin from producing a divide-by-zero/log(0)."""
    train_values = train_values[~np.isnan(train_values)]
    live_values = live_values[~np.isnan(live_values)]
    if len(train_values) < bins or len(live_values) == 0:
        return float("nan")

    quantiles = np.linspace(0, 100, bins + 1)
    edges = np.unique(np.percentile(train_values, quantiles))
    if len(edges) < 3:  # a near-constant feature has no meaningful bins to compare
        return 0.0

    train_counts, _ = np.histogram(train_values, bins=edges)
    live_counts, _ = np.histogram(live_values, bins=edges)
    eps = 1e-6
    train_pct = train_counts / train_counts.sum() + eps
    live_pct = live_counts / live_counts.sum() + eps
    return float(np.sum((live_pct - train_pct) * np.log(live_pct / train_pct)))


def _psi_status(psi: float) -> str:
    if np.isnan(psi):
        return "Insufficient Data"
    if psi >= PSI_SIGNIFICANT_THRESHOLD:
        return "Significant Drift"
    if psi >= PSI_DRIFTING_THRESHOLD:
        return "Drifting"
    return "Stable"


@lru_cache(maxsize=16)
def _cached_training_features(feature_version: str) -> pd.DataFrame | None:
    """The model's own training-split feature values, memoized per process -- loading
    the full historical feature set is too expensive to repeat per drift check. Reuses
    the same `load_feature_set` + `chronological_train_val_test_split` already used in
    `core.ml.registry.fit_and_store_calibration` and
    `core.ml.dataset_intelligence.training_validation_periods`."""
    from core.ml.cv import chronological_train_val_test_split
    from core.ml.feature_pipeline import load_feature_set

    try:
        features, _ = load_feature_set(feature_version)
        split = chronological_train_val_test_split(features)
    except Exception as exc:
        logger.warning("Could not load training features for drift check (feature_version=%s): %s", feature_version, exc)
        return None
    return features.loc[split.train_index]


def compute_feature_drift(
    feature_version: str, price_df: pd.DataFrame, sentiment_by_date: pd.Series | None = None,
    model_feature_names: list[str] | None = None, recent_window: int = 60, top_n: int = 5,
    symbol: str | None = None,
) -> list[FeatureDriftResult]:
    """PSI-based drift for a symbol's most recent `recent_window` days of features
    against the model's own training-split distribution for each feature, sorted by
    drift magnitude (most-drifted first). Returns an empty list (not fabricated Stable
    results) if the training distribution or live features can't be computed.

    When `symbol` is one of the tickers actually present in the training panel, the
    comparison uses *that symbol's own* training-period rows, not the full pooled
    multi-symbol panel -- comparing a single symbol's narrow recent window against a
    panel spanning 15 different tickers' cross-sectional differences would inflate PSI
    for reasons unrelated to real temporal drift (discovered empirically: normalized
    ratio features like `dist_from_52w_high` showed PSI > 10 purely from one symbol's
    lower variance vs. the pooled panel's). Falls back to the full panel (with a
    `used_pooled_panel` note implied by the absence of a per-symbol slice) only when the
    symbol wasn't part of the training panel at all -- still real evidence, just a
    coarser comparison."""
    from core.ml.feature_pipeline import build_features_v2

    training_features = _cached_training_features(feature_version)
    if training_features is None:
        return []

    if symbol is not None and isinstance(training_features.index, pd.MultiIndex) and "symbol" in training_features.index.names:
        symbol_levels = training_features.index.get_level_values("symbol")
        if symbol in symbol_levels:
            training_features = training_features[symbol_levels == symbol]

    try:
        live_features = build_features_v2(price_df, sentiment_by_date)
    except Exception as exc:
        logger.warning("Could not compute live features for drift check: %s", exc)
        return []
    if live_features.empty:
        return []
    live_recent = live_features.tail(recent_window)

    columns = model_feature_names if model_feature_names is not None else list(training_features.columns)
    results = []
    for col in columns:
        if col not in training_features.columns or col not in live_recent.columns:
            continue
        psi = _psi(training_features[col].to_numpy(dtype=float), live_recent[col].to_numpy(dtype=float))
        if np.isnan(psi):
            continue
        results.append(FeatureDriftResult(feature_name=col, psi=psi, status=_psi_status(psi)))

    results.sort(key=lambda r: r.psi, reverse=True)
    return results[:top_n] if top_n else results


def compute_prediction_drift(symbol: str, model_version: str) -> tuple[str, str | None]:
    """Compares the mean predicted probability_up of this model's most recent
    predictions for `symbol` against its earlier ones. Requires at least
    `MIN_PREDICTIONS_FOR_PREDICTION_DRIFT` predictions in total (recorded, resolved or
    not -- prediction drift is about the model's own outputs, not their correctness) to
    say anything meaningful; otherwise honestly reports Insufficient Data."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return "Insufficient Data", "No predictions recorded for this symbol yet."
        rows = session.execute(
            select(Prediction.probability, Prediction.date)
            .where(Prediction.ticker_id == ticker.id, Prediction.model_version == model_version)
            .order_by(Prediction.date)
        ).all()

    n = len(rows)
    if n < MIN_PREDICTIONS_FOR_PREDICTION_DRIFT:
        return "Insufficient Data", f"Only {n} live prediction(s) recorded for this symbol/model -- need at least {MIN_PREDICTIONS_FOR_PREDICTION_DRIFT} to judge prediction drift."

    probabilities = np.array([r[0] for r in rows])
    split_point = n // 2
    baseline_mean = float(probabilities[:split_point].mean())
    recent_mean = float(probabilities[split_point:].mean())
    shift = abs(recent_mean - baseline_mean)

    detail = f"Mean predicted P(up) shifted from {baseline_mean:.3f} (earlier half) to {recent_mean:.3f} (recent half) across {n} live predictions."
    if shift >= 0.25:
        return "Significant Drift", detail
    if shift >= 0.10:
        return "Drifting", detail
    return "Stable", detail


def compute_concept_drift(symbol: str, model_version: str, registered_accuracy: float | None) -> tuple[str, str | None]:
    """Compares live, resolved-outcome accuracy for this symbol/model against the
    accuracy the model was registered with (Phase 6). A meaningful drop indicates the
    real relationship between features and outcomes has shifted since training --
    concept drift, not just noisy predictions. Requires
    `MIN_RESOLVED_FOR_CONCEPT_DRIFT` resolved outcomes; otherwise Insufficient Data."""
    from core.ml.performance import overall_performance

    if registered_accuracy is None:
        return "Insufficient Data", "No registered evaluation accuracy available for this model version to compare against."

    stats = overall_performance(symbol=symbol, model_version=model_version)
    if stats.n < MIN_RESOLVED_FOR_CONCEPT_DRIFT:
        return "Insufficient Data", f"Only {stats.n} resolved live prediction(s) for this symbol/model -- need at least {MIN_RESOLVED_FOR_CONCEPT_DRIFT} to judge concept drift."

    drop = registered_accuracy - stats.accuracy
    detail = f"Live accuracy {stats.accuracy:.1%} over {stats.n} resolved predictions vs. registered eval accuracy {registered_accuracy:.1%} ({drop:+.1%} live minus registered, sign flipped: {-drop:+.1%})."
    if drop >= 0.15:
        return "Significant Drift", detail
    if drop >= 0.07:
        return "Drifting", detail
    return "Stable", detail


_STATUS_RANK = {"Stable": 0, "Insufficient Data": 0, "Drifting": 1, "Significant Drift": 2}


def _rollup_status(statuses: list[str]) -> str:
    non_insufficient = [s for s in statuses if s != "Insufficient Data"]
    if not non_insufficient:
        return "Insufficient Data"
    return max(non_insufficient, key=lambda s: _STATUS_RANK[s])


def assess_drift(
    symbol: str, model_version: str, feature_version: str | None, price_df: pd.DataFrame,
    registered_accuracy: float | None = None, model_feature_names: list[str] | None = None,
    sentiment_by_date: pd.Series | None = None,
) -> DriftReport:
    """The single entry point: runs all three drift checks and rolls them up into one
    report. Feature/data drift is the heaviest check (loads the full training feature
    set on first call per feature_version, memoized after); prediction/concept drift are
    cheap DB queries. Never raises -- a failed sub-check degrades to Insufficient Data
    for that check with a warning, never breaks the caller."""
    report = DriftReport()

    if feature_version is not None:
        try:
            feature_results = compute_feature_drift(feature_version, price_df, sentiment_by_date, model_feature_names, symbol=symbol)
            report.feature_drift = feature_results
            report.data_drift_status = _rollup_status([r.status for r in feature_results]) if feature_results else "Insufficient Data"
        except Exception as exc:
            logger.warning("Feature drift check failed for %s: %s", symbol, exc)
            report.warnings.append("Could not compute feature/data drift for this symbol.")
    else:
        report.warnings.append("No feature version available -- feature/data drift not assessed.")

    try:
        report.prediction_drift_status, report.prediction_drift_detail = compute_prediction_drift(symbol, model_version)
    except Exception as exc:
        logger.warning("Prediction drift check failed for %s: %s", symbol, exc)
        report.warnings.append("Could not compute prediction drift for this symbol.")

    try:
        report.concept_drift_status, report.concept_drift_detail = compute_concept_drift(symbol, model_version, registered_accuracy)
    except Exception as exc:
        logger.warning("Concept drift check failed for %s: %s", symbol, exc)
        report.warnings.append("Could not compute concept drift for this symbol.")

    report.overall_status = _rollup_status([report.data_drift_status, report.prediction_drift_status, report.concept_drift_status])
    report.recommend_retraining = report.overall_status == "Significant Drift"
    return report
