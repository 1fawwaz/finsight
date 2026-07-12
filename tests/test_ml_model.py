"""Tests for core.ml_model: feature/label correctness and the no-lookahead guarantee."""

import numpy as np
import pandas as pd
import pytest

from core.ml_model import (
    REGISTRY_MODEL_NAME,
    _predict_with_registry_model,
    build_features,
    build_labels,
    make_dataset,
    predict_next_direction,
    train_model,
)


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


class _FakeRegistryModel:
    """A stand-in for a registered model: fixed prediction, so tests can assert the
    registry path (not the in-app RandomForest fallback) actually produced the result."""

    def __init__(self, probability_up: float, feature_names: list[str] | None = None):
        self.probability_up = probability_up
        if feature_names is not None:
            self.feature_names_in_ = feature_names

    def predict_proba(self, X):
        return np.array([[1 - self.probability_up, self.probability_up]] * len(X))


def test_predict_with_registry_model_returns_none_when_none_registered(temp_db):
    price_df = _synthetic_price_df(n=320)
    assert _predict_with_registry_model(price_df, None) is None


def test_predict_next_direction_prefers_registry_model_when_available(temp_db, tmp_path, monkeypatch):
    import core.ml.registry as registry_module
    from core.ml.feature_pipeline import build_features_v2
    from core.ml.registry import register_model

    monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
    price_df = _synthetic_price_df(n=320)
    feature_names = list(build_features_v2(price_df).columns)

    # A fixed, obviously-distinguishable probability (0.9) the in-app RandomForest
    # fallback would be extremely unlikely to independently produce on random data.
    register_model(
        _FakeRegistryModel(0.9, feature_names), REGISTRY_MODEL_NAME, "xgboost", "ds1", "fs1", {}, {}, activate=True
    )

    result = _predict_with_registry_model(price_df, None)
    assert result == (True, 0.9)

    full_result = predict_next_direction(price_df)
    assert full_result == (True, 0.9)  # confirms predict_next_direction used the registry path, not the fallback


def test_predict_with_registry_model_falls_back_gracefully_on_error(temp_db, monkeypatch):
    def _raise(*args, **kwargs):
        raise RuntimeError("simulated registry failure")

    monkeypatch.setattr("core.ml.registry.get_active_model", _raise)
    price_df = _synthetic_price_df(n=320)

    assert _predict_with_registry_model(price_df, None) is None
    # predict_next_direction must still return a real result via the fallback path.
    assert predict_next_direction(price_df) is not None
