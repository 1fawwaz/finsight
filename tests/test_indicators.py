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
    parkinson_volatility,
    returns,
    rolling_variance,
    rsi,
    sma,
    support_resistance,
    true_range,
    volatility,
    volatility_percentile,
    volatility_regime,
    vwap,
    yang_zhang_volatility,
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


def test_rsi_short_history_returns_nan_not_crash():
    # Regression: a newly-listed stock with fewer trading days than the RSI window
    # (e.g. an IPO with 5 days of history) used to raise IndexError deep inside Wilder
    # smoothing instead of returning NaN like every other "not enough data yet" case.
    df = _make_ohlcv(5)
    result = rsi(df["close"], window=14)
    assert result.isna().all()


def test_atr_short_history_returns_nan_not_crash():
    df = _make_ohlcv(5)
    result = atr(df["high"], df["low"], df["close"], window=14)
    assert result.isna().all()


def test_adx_short_history_returns_nan_not_crash():
    df = _make_ohlcv(5)
    result = adx(df["high"], df["low"], df["close"], window=14)
    assert result.isna().all()


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


# --- Phase 2 Step 5: Volatility Features -------------------------------------------------


def test_rolling_variance_is_volatility_squared():
    """rolling_variance and volatility share the same underlying rolling std -- variance
    must equal the annualized volatility squared, not an independently-computed value
    that could silently drift out of sync."""
    df = _make_ohlcv(60)
    var = rolling_variance(df["close"], window=20, annualize=True)
    vol = volatility(df["close"], window=20, annualize=True)
    pd.testing.assert_series_equal(var, vol ** 2, check_names=False)


def test_rolling_variance_never_negative():
    df = _make_ohlcv(60)
    var = rolling_variance(df["close"], window=20)
    assert (var.dropna() >= 0).all()


def test_parkinson_volatility_never_negative():
    df = _make_ohlcv(60)
    vol = parkinson_volatility(df["high"], df["low"], window=20)
    assert (vol.dropna() >= 0).all()


def test_parkinson_volatility_matches_manual_formula():
    df = _make_ohlcv(30)
    window = 10
    result = parkinson_volatility(df["high"], df["low"], window=window, annualize=False)

    log_hl = np.log(df["high"] / df["low"])
    idx = 20
    window_vals = (log_hl.iloc[idx - window + 1 : idx + 1] ** 2) / (4 * math.log(2))
    expected = math.sqrt(window_vals.mean())
    assert result.iloc[idx] == pytest.approx(expected)


def test_parkinson_volatility_higher_for_wider_ranges():
    """A symbol with a consistently wider daily high-low range should show higher
    Parkinson volatility than one with a narrower range, all else equal."""
    dates = pd.bdate_range("2023-01-01", periods=30)
    close = pd.Series([100.0] * 30, index=dates)
    narrow_high, narrow_low = close + 0.5, close - 0.5
    wide_high, wide_low = close + 5.0, close - 5.0

    narrow_vol = parkinson_volatility(narrow_high, narrow_low, window=20).dropna()
    wide_vol = parkinson_volatility(wide_high, wide_low, window=20).dropna()

    assert (wide_vol > narrow_vol).all()


def test_yang_zhang_volatility_never_negative():
    # This file's _make_ohlcv helper doesn't produce an "open" column -- build one
    # directly rather than assuming a column that isn't there.
    df = _make_ohlcv(60)
    rng = np.random.default_rng(8)
    open_ = df["close"].shift(1).fillna(df["close"].iloc[0]) + rng.normal(0, 0.3, len(df))
    vol = yang_zhang_volatility(open_, df["high"], df["low"], df["close"], window=20)
    assert (vol.dropna() >= 0).all()


def test_yang_zhang_volatility_zero_for_perfectly_flat_series():
    """A perfectly flat series (no overnight gap, no intraday range, no drift) has zero
    true volatility by construction -- the estimator must recognize that exactly, not
    return a small nonzero artifact from floating-point noise in the formula."""
    dates = pd.bdate_range("2023-01-01", periods=30)
    flat = pd.Series([100.0] * 30, index=dates)
    vol = yang_zhang_volatility(flat, flat, flat, flat, window=20)
    assert (vol.dropna() < 1e-8).all()


def test_yang_zhang_volatility_reacts_to_overnight_gaps():
    """Parkinson (range-only) is blind to overnight gaps by construction; Yang-Zhang
    must pick up a real gap-driven volatility Parkinson would miss."""
    dates = pd.bdate_range("2023-01-01", periods=30)
    close = pd.Series([100.0] * 30, index=dates)
    open_flat = close.copy()
    # Alternate a +/-5% overnight gap every other day; identical, zero-range intraday
    # bars (open == high == low == close within each day) so Parkinson sees no range at
    # all, while Yang-Zhang's overnight term must still detect it.
    open_gapped = close.copy()
    open_gapped.iloc[1::2] = close.iloc[1::2] * 1.05

    yz_flat = yang_zhang_volatility(open_flat, close, close, close, window=20).dropna()
    yz_gapped = yang_zhang_volatility(open_gapped, open_gapped, open_gapped, open_gapped, window=20).dropna()

    assert (yz_gapped > yz_flat).all()


def test_volatility_percentile_bounded_0_to_1():
    df = _make_ohlcv(120)
    vol = volatility(df["close"], window=20)
    pct = volatility_percentile(vol, lookback=60)
    valid = pct.dropna()
    assert (valid >= 0.0).all() and (valid <= 1.0).all()


def test_volatility_percentile_is_1_at_the_series_max_so_far():
    """The single highest volatility value seen so far in the lookback window must rank
    at (or very near) the top percentile."""
    vol = pd.Series([0.1, 0.2, 0.15, 0.3, 0.25, 0.5])
    pct = volatility_percentile(vol, lookback=6)
    assert pct.iloc[-1] == pytest.approx(1.0)  # 0.5 is the max of the window


def test_volatility_regime_classifies_into_three_buckets():
    pct = pd.Series([0.1, 0.5, 0.9, np.nan])
    regime = volatility_regime(pct)
    assert regime.iloc[0] == "low"
    assert regime.iloc[1] == "medium"
    assert regime.iloc[2] == "high"
    assert pd.isna(regime.iloc[3])


def test_volatility_regime_thresholds_are_configurable():
    pct = pd.Series([0.5])
    assert volatility_regime(pct, low_threshold=0.6, high_threshold=0.8).iloc[0] == "low"
    assert volatility_regime(pct, low_threshold=0.2, high_threshold=0.4).iloc[0] == "high"
