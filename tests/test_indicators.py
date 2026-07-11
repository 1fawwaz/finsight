"""Tests for core.indicators, validated against hand/reference implementations of each formula."""

import math

import numpy as np
import pandas as pd
import pytest

from core.indicators import (
    adx,
    atr,
    bollinger_bands,
    ema,
    log_returns,
    macd,
    returns,
    rsi,
    sma,
    support_resistance,
    true_range,
    volatility,
    vwap,
)


def _reference_sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
        else:
            out.append(sum(values[i + 1 - window : i + 1]) / window)
    return out


def _reference_ema(values: list[float], span: int) -> list[float]:
    alpha = 2 / (span + 1)
    out = [values[0]]
    for x in values[1:]:
        out.append(alpha * x + (1 - alpha) * out[-1])
    return out


def _reference_wilder_rsi(values: list[float], window: int) -> list[float | None]:
    """Plain-Python reimplementation of Wilder's RSI, independent of the pandas ewm code path."""
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]

    out: list[float | None] = [None]  # no RSI for the first price
    avg_gain = avg_loss = None
    for i in range(len(deltas)):
        if i + 1 < window:
            out.append(None)
            continue
        if avg_gain is None:
            avg_gain = sum(gains[i + 1 - window : i + 1]) / window
            avg_loss = sum(losses[i + 1 - window : i + 1]) / window
        else:
            avg_gain = (avg_gain * (window - 1) + gains[i]) / window
            avg_loss = (avg_loss * (window - 1) + losses[i]) / window
        if avg_loss == 0:
            out.append(100.0)
        else:
            rs = avg_gain / avg_loss
            out.append(100 - 100 / (1 + rs))
    return out


def test_sma_matches_reference():
    values = [1, 2, 3, 4, 5, 6, 7, 8]
    expected = _reference_sma(values, window=3)
    result = sma(pd.Series(values, dtype=float), window=3)

    for i, exp in enumerate(expected):
        if exp is None:
            assert math.isnan(result.iloc[i])
        else:
            assert result.iloc[i] == pytest.approx(exp)


def test_ema_matches_reference():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    expected = _reference_ema(values, span=3)
    result = ema(pd.Series(values), span=3)

    for i in range(2, len(values)):  # min_periods=3 masks the first two
        assert result.iloc[i] == pytest.approx(expected[i], rel=1e-9)


def test_rsi_matches_wilder_reference():
    values = [44.0, 44.5, 44.25, 44.75, 44.5, 44.9, 45.1, 44.8, 45.3, 45.6]
    expected = _reference_wilder_rsi(values, window=3)
    result = rsi(pd.Series(values), window=3)

    for i, exp in enumerate(expected):
        if exp is None:
            assert math.isnan(result.iloc[i])
        else:
            assert result.iloc[i] == pytest.approx(exp, rel=1e-6)


def test_macd_line_is_ema_fast_minus_slow():
    values = pd.Series(np.linspace(100, 150, 60))
    result = macd(values, fast=12, slow=26, signal=9)

    expected_macd = ema(values, span=12) - ema(values, span=26)
    pd.testing.assert_series_equal(result["macd"], expected_macd, check_names=False)
    assert (result["histogram"].dropna() == (result["macd"] - result["signal"]).dropna()).all()


def test_bollinger_bands_width_matches_std_formula():
    values = pd.Series(np.random.default_rng(42).normal(100, 5, 50))
    bands = bollinger_bands(values, window=20, num_std=2.0)
    rolling_std = values.rolling(window=20, min_periods=20).std()

    upper_expected = bands["middle"] + 2.0 * rolling_std
    lower_expected = bands["middle"] - 2.0 * rolling_std
    pd.testing.assert_series_equal(bands["upper"], upper_expected, check_names=False)
    pd.testing.assert_series_equal(bands["lower"], lower_expected, check_names=False)


def test_returns_and_log_returns():
    values = pd.Series([100.0, 110.0, 99.0])
    simple = returns(values)
    log_r = log_returns(values)

    assert simple.iloc[1] == pytest.approx(0.10)
    assert simple.iloc[2] == pytest.approx(-0.1)
    assert log_r.iloc[1] == pytest.approx(math.log(1.10))


def test_volatility_annualization_factor():
    values = pd.Series(np.random.default_rng(1).normal(100, 1, 40))
    raw_std = returns(values).rolling(window=20, min_periods=20).std()
    annualized = volatility(values, window=20, annualize=True)

    ratio = (annualized.dropna() / raw_std.dropna()).unique()
    assert ratio == pytest.approx(math.sqrt(252), rel=1e-9)


def _make_ohlcv(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    high = close + rng.uniform(0.5, 2.0, n)
    low = close - rng.uniform(0.5, 2.0, n)
    volume = pd.Series(rng.integers(100_000, 1_000_000, n).astype(float))
    return pd.DataFrame({"high": high, "low": low, "close": close, "volume": volume})


def test_true_range_matches_manual_formula():
    df = _make_ohlcv(10)
    tr = true_range(df["high"], df["low"], df["close"])
    prev_close = df["close"].shift(1)
    for i in range(1, len(df)):
        expected = max(
            df["high"].iloc[i] - df["low"].iloc[i],
            abs(df["high"].iloc[i] - prev_close.iloc[i]),
            abs(df["low"].iloc[i] - prev_close.iloc[i]),
        )
        assert tr.iloc[i] == pytest.approx(expected)


def _reference_atr(high: list[float], low: list[float], close: list[float], window: int) -> list[float | None]:
    trs = []
    for i in range(len(close)):
        if i == 0:
            trs.append(high[i] - low[i])
        else:
            trs.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    out: list[float | None] = []
    avg = None
    for i in range(len(trs)):
        if i + 1 < window:
            out.append(None)
            continue
        if avg is None:
            avg = sum(trs[i + 1 - window : i + 1]) / window
        else:
            avg = (avg * (window - 1) + trs[i]) / window
        out.append(avg)
    return out


def test_atr_matches_wilder_reference():
    df = _make_ohlcv(15)
    expected = _reference_atr(df["high"].tolist(), df["low"].tolist(), df["close"].tolist(), window=5)
    result = atr(df["high"], df["low"], df["close"], window=5)

    for i, exp in enumerate(expected):
        if exp is None:
            assert math.isnan(result.iloc[i])
        else:
            assert result.iloc[i] == pytest.approx(exp, rel=1e-6)


def test_atr_is_never_negative():
    df = _make_ohlcv(40)
    result = atr(df["high"], df["low"], df["close"], window=14)
    assert (result.dropna() >= 0).all()


def test_adx_is_bounded_zero_to_hundred():
    df = _make_ohlcv(60)
    result = adx(df["high"], df["low"], df["close"], window=14)
    valid = result.dropna()
    assert len(valid) > 0
    assert (valid >= 0).all()
    assert (valid <= 100).all()


def test_vwap_matches_manual_rolling_calculation():
    df = _make_ohlcv(30)
    window = 10
    result = vwap(df["high"], df["low"], df["close"], df["volume"], window=window)

    typical = (df["high"] + df["low"] + df["close"]) / 3
    idx = 15
    expected = (typical.iloc[idx - window + 1 : idx + 1] * df["volume"].iloc[idx - window + 1 : idx + 1]).sum() / df[
        "volume"
    ].iloc[idx - window + 1 : idx + 1].sum()
    assert result.iloc[idx] == pytest.approx(expected)


def test_support_resistance_are_rolling_min_max():
    df = _make_ohlcv(30)
    window = 10
    bands = support_resistance(df["high"], df["low"], window=window)

    expected_support = df["low"].rolling(window=window, min_periods=window).min()
    expected_resistance = df["high"].rolling(window=window, min_periods=window).max()
    pd.testing.assert_series_equal(bands["support"], expected_support, check_names=False)
    pd.testing.assert_series_equal(bands["resistance"], expected_resistance, check_names=False)


def test_support_is_never_above_resistance():
    df = _make_ohlcv(40)
    bands = support_resistance(df["high"], df["low"], window=20)
    valid = bands.dropna()
    assert (valid["support"] <= valid["resistance"]).all()
