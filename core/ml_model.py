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

from core.indicators import macd, rsi, volatility

RANDOM_STATE = 42


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
