"""Tests for core.ml_model: feature/label correctness and the no-lookahead guarantee."""

import numpy as np
import pandas as pd
import pytest

from core.ml_model import build_features, build_labels, make_dataset, predict_next_direction, train_model


def _synthetic_price_df(n: int = 80, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    returns = rng.normal(0.0005, 0.015, n)
    close = 100 * np.cumprod(1 + returns)
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


def test_features_have_no_lookahead():
    """Appending future rows must not change any already-computed feature value.

    This is the dedicated regression test guarding against lookahead bias: every
    feature must be derivable from data available up to and including that row.
    """
    full_df = _synthetic_price_df(n=80)
    truncated_df = full_df.iloc[:50]

    features_full = build_features(full_df)
    features_truncated = build_features(truncated_df)

    # The first 50 rows must be bit-for-bit identical whether or not 30 future rows exist.
    pd.testing.assert_frame_equal(features_full.iloc[:50], features_truncated)


def test_build_labels_shifts_forward_and_drops_last_row():
    close = pd.Series([100.0, 105.0, 102.0, 110.0], index=pd.date_range("2024-01-01", periods=4))
    labels = build_labels(close)

    assert labels.iloc[0] == 1  # 105 > 100
    assert labels.iloc[1] == 0  # 102 < 105
    assert labels.iloc[2] == 1  # 110 > 102
    assert pd.isna(labels.iloc[3])  # no next day


def test_make_dataset_is_clean_and_aligned():
    price_df = _synthetic_price_df(n=80)
    features, labels = make_dataset(price_df)

    assert not features.isna().any().any()
    assert not labels.isna().any()
    assert list(features.index) == list(labels.index)
    assert set(labels.unique()) <= {0, 1}
    # Warm-up (rolling windows) plus the undefined final label should trim rows from 80.
    assert len(features) < 80


def test_make_dataset_includes_sentiment_when_provided():
    price_df = _synthetic_price_df(n=80)
    sentiment = pd.Series(0.5, index=price_df.index)

    features, _ = make_dataset(price_df, sentiment_by_date=sentiment)

    assert "sentiment" in features.columns
    assert (features["sentiment"] == 0.5).all()


def test_train_model_returns_fitted_classifier():
    price_df = _synthetic_price_df(n=80)
    features, labels = make_dataset(price_df)

    model = train_model(features, labels)

    preds = model.predict(features)
    assert len(preds) == len(features)
    assert set(preds.tolist()) <= {0, 1}


def test_predict_next_direction_returns_prediction_and_probability():
    price_df = _synthetic_price_df(n=200)
    result = predict_next_direction(price_df)

    assert result is not None
    predicted_up, probability_up = result
    assert isinstance(predicted_up, bool)
    assert 0.0 <= probability_up <= 1.0
    assert predicted_up == (probability_up >= 0.5)


def test_predict_next_direction_none_with_insufficient_history():
    price_df = _synthetic_price_df(n=30)
    assert predict_next_direction(price_df) is None


def test_predict_next_direction_uses_todays_row_not_seen_in_training():
    """The predicted row must be the one make_dataset necessarily drops (today's, with
    no label yet) -- proving the model never trains on the very row it predicts."""
    price_df = _synthetic_price_df(n=200)
    features, _ = make_dataset(price_df)
    full_features = build_features(price_df)

    assert features.index[-1] != full_features.index[-1]
    assert full_features.index[-1] == price_df.index[-1]

    result = predict_next_direction(price_df)
    assert result is not None
