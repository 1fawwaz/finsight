"""Tests for core.backtester: strict chronological walk-forward splits and metric/equity correctness."""

import numpy as np
import pandas as pd
import pytest

from core.backtester import walk_forward_backtest
from core.ml_model import make_dataset


def _synthetic_price_df(n: int = 150, seed: int = 11) -> pd.DataFrame:
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


def test_walk_forward_folds_never_train_on_future_data(monkeypatch):
    price_df = _synthetic_price_df(n=150)
    features, labels = make_dataset(price_df)

    seen_splits = []
    from core import backtester as backtester_module

    real_train_model = backtester_module.train_model

    def _spy_train_model(X_train, y_train):
        seen_splits.append((X_train.index.max(), y_train))
        return real_train_model(X_train, y_train)

    monkeypatch.setattr(backtester_module, "train_model", _spy_train_model)

    result = walk_forward_backtest(features, labels, price_df["close"], train_window=40, test_window=10)

    assert len(seen_splits) > 0
    # Reconstruct each fold's test-window start the same way the backtester does, and confirm
    # every fold's training data ends strictly before that fold's test data begins.
    i = 40
    fold_idx = 0
    while i + 10 <= len(features):
        train_end_date = seen_splits[fold_idx][0]
        test_start_date = features.index[i]
        assert train_end_date < test_start_date
        i += 10
        fold_idx += 1


def test_walk_forward_backtest_produces_valid_metrics_and_equity_curves():
    price_df = _synthetic_price_df(n=150)
    features, labels = make_dataset(price_df)

    result = walk_forward_backtest(features, labels, price_df["close"], train_window=40, test_window=10)

    assert 0.0 <= result.accuracy <= 1.0
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    assert result.confusion.shape == (2, 2)
    assert set(result.predictions["predicted"].unique()) <= {0, 1}
    assert len(result.predictions) > 0

    # Equity curves are cumulative growth factors -> always positive, same length as predictions.
    assert (result.equity_signal > 0).all()
    assert (result.equity_buy_hold > 0).all()
    assert len(result.equity_signal) == len(result.predictions)
    assert len(result.equity_buy_hold) == len(result.predictions)


def test_walk_forward_backtest_predictions_are_chronologically_sorted():
    price_df = _synthetic_price_df(n=150)
    features, labels = make_dataset(price_df)

    result = walk_forward_backtest(features, labels, price_df["close"], train_window=40, test_window=10)

    dates = result.predictions.index
    assert list(dates) == sorted(dates)


def test_walk_forward_backtest_raises_with_insufficient_history():
    price_df = _synthetic_price_df(n=30)
    features, labels = make_dataset(price_df)

    with pytest.raises(ValueError):
        walk_forward_backtest(features, labels, price_df["close"], train_window=40, test_window=10)
