"""Feature engineering and direction classifier for FinSight's ML Signals page.

Every feature at row t is computed using only price/volume data available up to and
including day t (rolling/lagged windows looking backward). The label at row t is the
direction of the move from day t to day t+1, so the last row always has an undefined
label and is dropped. See tests/test_ml_model.py::test_features_have_no_lookahead for
a dedicated regression test proving feature values don't change when future rows are
appended.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

from core.config import get_logger
from core.indicators import macd, rsi, volatility

logger = get_logger(__name__)

RANDOM_STATE = 42
REGISTRY_MODEL_NAME = "finsight_direction_classifier"


def build_features(price_df: pd.DataFrame, sentiment_by_date: Optional[pd.Series] = None) -> pd.DataFrame:
    """Engineered features from OHLCV data, optionally enriched with a daily sentiment series.

    `price_df` must have `close` and `volume` columns, date-indexed and sorted ascending.
    """
    close = price_df["close"]
    volume = price_df["volume"]

    features = pd.DataFrame(index=price_df.index)
    features["lag_return_1"] = close.pct_change(1)
    features["lag_return_2"] = close.pct_change(2)
    features["lag_return_3"] = close.pct_change(3)
    features["lag_return_5"] = close.pct_change(5)

    volume_mean = volume.rolling(window=20, min_periods=20).mean()
    volume_std = volume.rolling(window=20, min_periods=20).std()
    features["volume_zscore"] = (volume - volume_mean) / volume_std

    features["rsi_14"] = rsi(close, window=14)

    macd_df = macd(close)
    features["macd"] = macd_df["macd"]
    features["macd_signal"] = macd_df["signal"]

    features["volatility_20"] = volatility(close, window=20, annualize=True)

    if sentiment_by_date is not None:
        features["sentiment"] = sentiment_by_date.reindex(features.index).fillna(0.0)

    return features


def build_labels(close: pd.Series) -> pd.Series:
    """1 if the next day's close is higher than today's, else 0. Last row is NaN (no next day)."""
    next_close = close.shift(-1)
    label = (next_close > close).astype(float)
    return label.where(next_close.notna())


def make_dataset(
    price_df: pd.DataFrame, sentiment_by_date: Optional[pd.Series] = None
) -> tuple[pd.DataFrame, pd.Series]:
    """Build a clean, aligned (features, labels) dataset with warm-up and trailing NaN rows dropped."""
    features = build_features(price_df, sentiment_by_date)
    labels = build_labels(price_df["close"])
    combined = features.join(labels.rename("label")).dropna()
    return combined.drop(columns=["label"]), combined["label"].astype(int)


def train_model(features: pd.DataFrame, labels: pd.Series) -> RandomForestClassifier:
    """Fit a RandomForest direction classifier. Shallow trees + a min-leaf floor to resist overfitting noise."""
    model = RandomForestClassifier(
        n_estimators=200,
        max_depth=5,
        min_samples_leaf=10,
        random_state=RANDOM_STATE,
    )
    model.fit(features, labels)
    return model


def _predict_with_registry_model(
    price_df: pd.DataFrame, sentiment_by_date: Optional[pd.Series]
) -> Optional[tuple[bool, float]]:
    """Try the Phase 3 registered model (core.ml.registry) first -- on a fair,
    identical held-out test fold it beat this module's in-app RandomForest (ROC-AUC
    0.515 vs 0.491; see core/ml/registry.py's registration commit for the comparison).
    Imports are deferred to the function body: core.ml.feature_pipeline imports
    build_labels from this module, so a module-level import here would be circular.
    Returns None (not an exception) on any failure, so the caller always has a working
    fallback -- a registry/feature mismatch must never break a live prediction.
    """
    try:
        from core.ml.feature_pipeline import build_features_v2
        from core.ml.registry import get_active_model

        model, _entry = get_active_model(REGISTRY_MODEL_NAME)
        if model is None:
            return None

        latest_features = build_features_v2(price_df, sentiment_by_date).iloc[[-1]]
        if hasattr(model, "feature_names_in_"):
            latest_features = latest_features.reindex(columns=model.feature_names_in_)
        if latest_features.isna().to_numpy().any():
            return None

        probability_up = float(model.predict_proba(latest_features)[0][1])
        return probability_up >= 0.5, probability_up
    except Exception as exc:  # registry/feature-pipeline errors must never break a live prediction
        logger.warning("Registry model prediction unavailable, falling back to in-app RandomForest: %s", exc)
        return None


def predict_next_direction(
    price_df: pd.DataFrame, sentiment_by_date: Optional[pd.Series] = None
) -> Optional[tuple[bool, float]]:
    """Predict the next trading session's direction. Prefers the Phase 3 registered
    model (core.ml.registry) when one is active and its features compute cleanly for
    this symbol; otherwise falls back to training an in-app RandomForest fresh on this
    symbol's own history, exactly as before -- no registered model is required for this
    function to work.

    Returns (predicted_up, probability_up), or None if there's too little history to
    predict from, for either path.
    """
    registry_result = _predict_with_registry_model(price_df, sentiment_by_date)
    if registry_result is not None:
        return registry_result

    features, labels = make_dataset(price_df, sentiment_by_date)
    if len(features) < 50:
        return None

    latest_features = build_features(price_df, sentiment_by_date).iloc[[-1]]
    if latest_features.isna().to_numpy().any():
        return None

    model = train_model(features, labels)
    probability_up = float(model.predict_proba(latest_features)[0][list(model.classes_).index(1)])
    return probability_up >= 0.5, probability_up
