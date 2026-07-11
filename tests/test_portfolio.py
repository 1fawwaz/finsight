"""Tests for core.portfolio, validated against hand-computed reference values."""

import math

import numpy as np
import pandas as pd
import pytest

from core.portfolio import (
    correlation_matrix,
    cumulative_returns,
    max_drawdown,
    portfolio_daily_returns,
    portfolio_weights,
    position_values,
    sharpe_ratio,
)


def test_position_values_and_weights():
    shares = {"RELIANCE.NS": 10, "TCS.NS": 5}
    prices = {"RELIANCE.NS": 100.0, "TCS.NS": 200.0}

    values = position_values(shares, prices)
    assert values == {"RELIANCE.NS": 1000.0, "TCS.NS": 1000.0}

    weights = portfolio_weights(shares, prices)
    assert weights["RELIANCE.NS"] == pytest.approx(0.5)
    assert weights["TCS.NS"] == pytest.approx(0.5)


def test_portfolio_weights_zero_total_returns_zero_weights():
    shares = {"RELIANCE.NS": 0, "TCS.NS": 0}
    prices = {"RELIANCE.NS": 100.0, "TCS.NS": 200.0}
    weights = portfolio_weights(shares, prices)
    assert weights == {"RELIANCE.NS": 0.0, "TCS.NS": 0.0}


def test_cumulative_returns():
    close = pd.Series([100.0, 110.0, 121.0, 99.0])
    result = cumulative_returns(close)
    expected = [0.0, 0.10, 0.21, -0.01]
    for got, exp in zip(result, expected):
        assert got == pytest.approx(exp)


def test_max_drawdown_known_peak_trough():
    # Peak at 100 (idx1), trough at 50 (idx3) => -50% drawdown.
    close = pd.Series([90.0, 100.0, 80.0, 50.0, 70.0])
    result = max_drawdown(close)
    assert result == pytest.approx(-0.5)


def test_max_drawdown_monotonic_increase_is_zero():
    close = pd.Series([10.0, 20.0, 30.0, 40.0])
    assert max_drawdown(close) == pytest.approx(0.0)


def test_sharpe_ratio_matches_manual_calculation():
    daily = pd.Series([0.01, -0.005, 0.02, 0.0, 0.015])
    mean = daily.mean()
    std = daily.std()
    expected = (mean / std) * math.sqrt(252)

    result = sharpe_ratio(daily, risk_free_rate=0.0, annualize=True)
    assert result == pytest.approx(expected, rel=1e-9)


def test_sharpe_ratio_zero_std_is_zero():
    daily = pd.Series([0.01, 0.01, 0.01])
    assert sharpe_ratio(daily) == 0.0


def test_correlation_matrix_perfectly_correlated_and_anticorrelated():
    base = pd.Series([100.0, 101.0, 102.5, 101.5, 103.0])
    a_returns = base.pct_change().fillna(0.0)

    # C's returns are exactly the negation of A's returns => correlation -1.
    c_prices = [100.0]
    for r in a_returns.iloc[1:]:
        c_prices.append(c_prices[-1] * (1 - r))

    df = pd.DataFrame(
        {
            "A": base,
            "B": base * 2,  # identical returns => correlation 1
            "C": c_prices,
        }
    )
    corr = correlation_matrix(df)

    assert corr.loc["A", "B"] == pytest.approx(1.0, abs=1e-9)
    assert corr.loc["A", "C"] == pytest.approx(-1.0, abs=1e-9)
    assert corr.loc["A", "A"] == pytest.approx(1.0)


def test_portfolio_daily_returns_weighted_average():
    df = pd.DataFrame(
        {
            "A": [100.0, 110.0, 121.0],
            "B": [50.0, 45.0, 49.5],
        }
    )
    weights = {"A": 0.5, "B": 0.5}
    result = portfolio_daily_returns(df, weights)

    a_returns = df["A"].pct_change().dropna()
    b_returns = df["B"].pct_change().dropna()
    expected = 0.5 * a_returns + 0.5 * b_returns

    pd.testing.assert_series_equal(result, expected, check_names=False)
