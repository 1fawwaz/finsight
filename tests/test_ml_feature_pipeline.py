"""Tests for core.ml.feature_pipeline: extended feature engineering + SQLite feature store."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from core.database import Price, Ticker, get_session
from core.ml.feature_pipeline import (
    build_features_v2,
    load_feature_set,
    make_dataset_v2,
    persist_feature_set,
)


def _make_ohlcv(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    open_ = close + rng.normal(0, 0.5, n)
    volume = rng.integers(1_000_000, 5_000_000, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


def test_build_features_v2_has_no_lookahead():
    # Appending a future row must not change any already-computed feature value for
    # earlier dates -- every window here is backward-looking only.
    df = _make_ohlcv(300)
    truncated = df.iloc[:-1]

    full_features = build_features_v2(df)
    truncated_features = build_features_v2(truncated)

    common_index = truncated_features.index
    pd.testing.assert_frame_equal(full_features.loc[common_index], truncated_features, check_exact=True)


def test_build_features_v2_produces_expected_columns():
    df = _make_ohlcv(300)
    features = build_features_v2(df)
    expected = {
        "lag_return_1", "lag_return_2", "lag_return_3", "lag_return_5", "volume_zscore",
        "rsi_14", "macd", "macd_signal", "volatility_20",
        "atr_14", "adx_14", "price_to_vwap", "bollinger_pct_b", "bollinger_bandwidth",
        "sma_20_dist", "ema_20_dist", "roc_10", "momentum_10", "volume_ratio_5_20",
        "gap_pct", "candle_body_pct", "upper_wick_pct", "lower_wick_pct",
        "dist_from_support", "dist_from_resistance", "dist_from_52w_high", "dist_from_52w_low",
    }
    assert expected.issubset(set(features.columns))


def test_build_features_v2_clean_data_has_no_nan_after_warmup():
    df = _make_ohlcv(300)
    features = build_features_v2(df)
    # Past the longest warm-up window (252 for the 52-week distance features), every
    # column should be fully populated for well-formed OHLCV input.
    tail = features.iloc[260:]
    assert not tail.isna().any().any()


def test_build_features_v2_with_sentiment_adds_column():
    df = _make_ohlcv(300)
    sentiment = pd.Series(0.5, index=df.index)
    features = build_features_v2(df, sentiment_by_date=sentiment)
    assert "sentiment" in features.columns
    assert (features["sentiment"].dropna() == 0.5).all()


def test_make_dataset_v2_drops_last_row_and_aligns_labels():
    df = _make_ohlcv(300)
    features, labels = make_dataset_v2(df)
    assert len(features) == len(labels)
    assert set(labels.unique()).issubset({0, 1})
    assert df.index[-1] not in features.index


def _seed_ticker(session, symbol: str, n: int = 300) -> None:
    ticker = Ticker(symbol=symbol, name=symbol, sector="Technology")
    session.add(ticker)
    session.flush()
    start = date(2023, 1, 1)
    for i in range(n):
        session.add(
            Price(
                ticker_id=ticker.id,
                date=start + timedelta(days=i),
                open=100.0 + i * 0.05,
                high=101.5 + i * 0.05,
                low=98.5 + i * 0.05,
                close=100.2 + i * 0.05,
                volume=1_000_000 + i * 100,
            )
        )


def test_persist_and_load_feature_set_roundtrips(temp_db):
    with get_session() as session:
        _seed_ticker(session, "ROUNDTRIP.NS", 300)

    df = _make_ohlcv(300)
    features, labels = make_dataset_v2(df)

    persisted = persist_feature_set(
        dataset_version="test_dataset_v1",
        features_by_symbol={"ROUNDTRIP.NS": features},
        labels_by_symbol={"ROUNDTRIP.NS": labels},
        feature_version="test_features_v1",
    )
    assert persisted.row_count == len(features)
    assert persisted.dataset_version == "test_dataset_v1"

    loaded_X, loaded_y = load_feature_set("test_features_v1")
    assert len(loaded_X) == len(features)
    assert set(loaded_y.unique()).issubset({0, 1})
    assert set(loaded_X.columns) == set(features.columns)


def test_load_feature_set_unknown_version_raises(temp_db):
    with pytest.raises(ValueError, match="No feature set"):
        load_feature_set("does_not_exist")


def test_persist_feature_set_skips_symbol_with_no_ticker_row(temp_db, caplog):
    df = _make_ohlcv(300)
    features, labels = make_dataset_v2(df)
    # No Ticker row exists for "GHOST.NS" -- must be skipped, not crash.
    persisted = persist_feature_set(
        dataset_version="test_dataset_v2",
        features_by_symbol={"GHOST.NS": features},
        labels_by_symbol={"GHOST.NS": labels},
        feature_version="test_features_v2",
    )
    assert persisted is not None
