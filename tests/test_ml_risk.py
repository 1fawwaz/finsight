"""Tests for core/ml/risk.py -- risk assessment derived from real OHLCV data (and, when
available, a real fitted model for stability) -- never a fabricated risk figure."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

from core.ml.risk import RISK_LEVELS, assess_risk


def _ohlcv(n=300, seed=1, trend=0.0, vol=1.0):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(trend, vol, n))
    close = np.maximum(close, 1.0)
    high = close + rng.uniform(0, vol, n)
    low = close - rng.uniform(0, vol, n)
    volume = rng.integers(100_000, 500_000, n)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": volume}, index=dates)


class TestAssessRiskWithoutModel:
    def test_returns_a_full_assessment_from_price_data_alone(self):
        risk = assess_risk(_ohlcv())
        assert risk.risk_level in RISK_LEVELS
        assert 0.0 <= risk.risk_score <= 100.0
        assert 0.0 <= risk.volatility_score <= 100.0
        assert risk.volatility_annualized >= 0.0

    def test_stability_is_reported_as_unmeasured_when_no_model_given(self):
        risk = assess_risk(_ohlcv())
        assert risk.prediction_stability == 50.0
        assert "not measured" in risk.method_notes

    def test_higher_volatility_series_scores_a_higher_risk_score(self):
        calm = assess_risk(_ohlcv(seed=2, vol=0.3))
        wild = assess_risk(_ohlcv(seed=2, vol=8.0))
        assert wild.volatility_annualized > calm.volatility_annualized
        assert wild.risk_score > calm.risk_score

    def test_expected_drawdown_is_negative_or_zero(self):
        risk = assess_risk(_ohlcv())
        assert risk.expected_drawdown <= 0.0

    def test_expected_upside_is_positive_or_zero(self):
        risk = assess_risk(_ohlcv())
        assert risk.expected_upside >= 0.0

    def test_short_history_degrades_gracefully_not_a_crash(self):
        short = _ohlcv(n=3)
        risk = assess_risk(short)
        assert risk.expected_drawdown == 0.0
        assert risk.expected_upside == 0.0
        assert "insufficient history" in risk.method_notes

    def test_market_regime_is_a_nonempty_label(self):
        risk = assess_risk(_ohlcv())
        assert isinstance(risk.market_regime, str)
        assert len(risk.market_regime) > 0


class TestAssessRiskWithModel:
    def _fitted_model_and_row(self):
        rng = np.random.default_rng(9)
        X = pd.DataFrame({"a": rng.normal(size=150), "b": rng.normal(size=150)})
        y = (X["a"] > 0).astype(int)
        model = RandomForestClassifier(n_estimators=15, max_depth=3, random_state=0).fit(X, y)
        return model, X.iloc[[0]]

    def test_stability_is_actually_measured_when_model_given(self):
        model, row = self._fitted_model_and_row()
        risk = assess_risk(_ohlcv(), model=model, feature_row=row)
        assert "measured via" in risk.method_notes
        assert 0.0 <= risk.prediction_stability <= 100.0

    def test_a_perfectly_confident_constant_model_is_reported_as_stable(self):
        class _ConstantModel:
            def predict_proba(self, X):
                return np.tile([0.1, 0.9], (len(X), 1))

        row = pd.DataFrame({"a": [1.0], "b": [2.0]})
        risk = assess_risk(_ohlcv(), model=_ConstantModel(), feature_row=row)
        assert risk.prediction_stability == pytest.approx(100.0, abs=1.0)

    def test_confidence_penalty_is_bounded(self):
        model, row = self._fitted_model_and_row()
        risk = assess_risk(_ohlcv(), model=model, feature_row=row)
        assert 0.0 <= risk.confidence_penalty <= 100.0
