"""Tests for core.ml.labels: Phase 2 Step 1 candidate label definitions."""

import numpy as np
import pandas as pd
import pytest

from core.ml_model import build_labels
from core.ml.labels import (
    atr_adjusted_labels,
    compare_label_candidates,
    fixed_horizon_labels,
    meta_labels,
    threshold_labels,
    to_binary,
    triple_barrier_labels,
    volatility_adjusted_labels,
)


def _close_series(values: list[float]) -> pd.Series:
    index = pd.date_range("2024-01-01", periods=len(values), freq="D")
    return pd.Series(values, index=index)


def _ohlc(close_values: list[float], high_pad: float = 1.0, low_pad: float = 1.0):
    close = _close_series(close_values)
    high = close + high_pad
    low = close - low_pad
    return close, high, low


def test_fixed_horizon_labels_matches_existing_build_labels_at_horizon_1():
    close = _close_series([100, 105, 103, 110, 108, 108])
    assert (fixed_horizon_labels(close, horizon=1).dropna() == build_labels(close).dropna()).all()
    pd.testing.assert_series_equal(fixed_horizon_labels(close, horizon=1), build_labels(close))


def test_fixed_horizon_labels_multi_day_horizon():
    close = _close_series([100, 101, 102, 90, 95])
    label = fixed_horizon_labels(close, horizon=2)
    # row 0: close[2]=102 > close[0]=100 -> 1; row 1: close[3]=90 < close[1]=101 -> 0
    assert label.iloc[0] == 1.0
    assert label.iloc[1] == 0.0
    assert label.iloc[-2:].isna().all()  # last `horizon` rows have no future close


def test_atr_adjusted_labels_large_move_is_up():
    # Build 20 rows of flat data (for a stable, non-NaN ATR) then a large jump.
    close_values = [100.0] * 20 + [130.0]
    close, high, low = _ohlc(close_values)
    label = atr_adjusted_labels(close, high, low, horizon=1, atr_window=14, k=0.25)
    assert label.iloc[19] == 1.0  # the day before the jump should classify the jump as "up"


def test_atr_adjusted_labels_small_move_is_neutral():
    close_values = [100.0] * 20 + [100.05]  # trivially small move relative to a 2-point ATR
    close, high, low = _ohlc(close_values, high_pad=1.0, low_pad=1.0)
    label = atr_adjusted_labels(close, high, low, horizon=1, atr_window=14, k=0.25)
    assert label.iloc[19] == 0.0


def test_volatility_adjusted_labels_large_move_is_up():
    close_values = [100.0, 100.5, 99.5, 100.2, 99.8] * 5 + [130.0]
    close = _close_series(close_values)
    label = volatility_adjusted_labels(close, horizon=1, vol_window=20, k=0.25)
    assert label.iloc[-2] == 1.0


def test_threshold_labels_classifies_by_fixed_percentage():
    close = _close_series([100, 102, 100.5, 90])
    label = threshold_labels(close, horizon=1, threshold=0.01)
    assert label.iloc[0] == 1.0   # +2% > 1%
    assert label.iloc[1] == -1.0  # -1.47% < -1%
    assert label.iloc[2] == -1.0  # -10% < -1%


def test_threshold_labels_neutral_within_band():
    close = _close_series([100, 100.5])  # +0.5%, inside a 1% threshold
    label = threshold_labels(close, horizon=1, threshold=0.01)
    assert label.iloc[0] == 0.0


def test_triple_barrier_hits_upper_first():
    # Flat warm-up for ATR, then a clear upward path within the horizon.
    close_values = [100.0] * 15 + [100.0, 101.0, 103.0, 110.0, 108.0]
    close, high, low = _ohlc(close_values)
    result = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    t = 15  # the row right after warm-up, close=100.0, upper barrier ~104 (2*2 ATR-pct)
    assert result.labels.iloc[t] == 1.0
    assert result.barrier_hit.iloc[t] == "upper"


def test_triple_barrier_hits_lower_first():
    close_values = [100.0] * 15 + [100.0, 99.0, 97.0, 90.0, 92.0]
    close, high, low = _ohlc(close_values)
    result = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    t = 15
    assert result.labels.iloc[t] == -1.0
    assert result.barrier_hit.iloc[t] == "lower"


def test_triple_barrier_vertical_when_neither_barrier_touched():
    close_values = [100.0] * 15 + [100.0, 100.1, 99.9, 100.2, 99.8]
    close, high, low = _ohlc(close_values)
    result = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    t = 15
    assert result.labels.iloc[t] == 0.0
    assert result.barrier_hit.iloc[t] == "vertical"


def test_triple_barrier_does_not_use_data_beyond_the_horizon_window():
    """A large move that only happens *after* the horizon window must not influence the
    label -- proving the method respects its own declared window, not an open-ended scan.
    With t=15 and horizon=5, the window is days t+1..t+5 (indices 16-20); the spike must
    sit at t+6 (index 21) to be genuinely out-of-window."""
    close_values = [100.0] * 15 + [100.0, 100.1, 99.9, 100.2, 99.8, 99.9, 999.0]
    close, high, low = _ohlc(close_values)
    result = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    t = 15
    assert result.labels.iloc[t] == 0.0  # vertical -- the out-of-window spike must not count
    assert result.barrier_hit.iloc[t] == "vertical"


def test_meta_labels_correct_when_primary_side_matches_outcome():
    close_values = [100.0] * 15 + [100.0, 101.0, 103.0, 110.0, 108.0]
    close, high, low = _ohlc(close_values)
    triple_barrier = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    primary_side = pd.Series(1.0, index=close.index)  # always bets "up"

    meta = meta_labels(primary_side, triple_barrier)

    assert meta.iloc[15] == 1.0  # primary bet "up", outcome was "up" (upper barrier) -- correct bet


def test_meta_labels_incorrect_when_primary_side_mismatches_outcome():
    close_values = [100.0] * 15 + [100.0, 99.0, 97.0, 90.0, 92.0]
    close, high, low = _ohlc(close_values)
    triple_barrier = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    primary_side = pd.Series(1.0, index=close.index)  # always bets "up", but outcome is "down"

    meta = meta_labels(primary_side, triple_barrier)

    assert meta.iloc[15] == 0.0  # wrong bet


def test_meta_labels_nan_on_vertical_no_resolution():
    close_values = [100.0] * 15 + [100.0, 100.1, 99.9, 100.2, 99.8]
    close, high, low = _ohlc(close_values)
    triple_barrier = triple_barrier_labels(close, high, low, horizon=5, upper_k=2.0, lower_k=2.0, atr_window=14)
    primary_side = pd.Series(1.0, index=close.index)

    meta = meta_labels(primary_side, triple_barrier)

    assert pd.isna(meta.iloc[15])  # no resolution -- meta-label undefined, not fabricated as 0 or 1


def test_to_binary_drops_neutral_and_maps_signs():
    label = pd.Series([1.0, -1.0, 0.0, 1.0])
    binary = to_binary(label)
    assert binary.iloc[0] == 1
    assert binary.iloc[1] == 0
    assert pd.isna(binary.iloc[2])
    assert binary.iloc[3] == 1


def test_compare_label_candidates_runs_end_to_end_on_synthetic_data():
    """Not a claim about which label wins (that's real data's job) -- proves the
    comparison harness itself runs cleanly end-to-end: real features, a real
    chronological split, a real fitted model, real metrics, for every candidate."""
    n = 400
    rng = np.random.default_rng(42)
    index = pd.date_range("2023-01-01", periods=n, freq="D")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=index)
    close = close.clip(lower=10)  # keep prices positive
    price_df = pd.DataFrame(
        {
            "open": close, "high": close + rng.uniform(0.5, 2, n), "low": close - rng.uniform(0.5, 2, n),
            "close": close, "volume": rng.integers(1_000_000, 5_000_000, n),
        },
        index=index,
    )

    results = compare_label_candidates(price_df)

    assert len(results) == 6  # every candidate produced a result, none silently skipped
    for r in results:
        assert 0.0 <= r.model_accuracy <= 1.0
        assert r.n_labeled_rows > 0 or r.note != ""
