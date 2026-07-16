"""Tests for core/ml/recommendation.py -- synthesizing already-computed prediction
fields into a plain-language research summary. Verifies it never fabricates a
recommendation beyond what the underlying fields actually support."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from core.ml.confidence import assess_confidence
from core.ml.performance import PerformanceStats
from core.ml.prediction_service import PredictionResult
from core.ml.recommendation import build_recommendation
from core.ml.risk import RiskAssessment


def _base_result(prob_up: float = 0.7) -> PredictionResult:
    result = PredictionResult(symbol="TEST.NS", generated_at=datetime.now(timezone.utc))
    result.confidence = assess_confidence(prob_up, was_calibrated=True)
    result.model_source = "registry"
    result.model_name = "test_model"
    result.model_version = "test_model_v1"
    return result


def _fake_risk(expected_drawdown=-0.12, expected_upside=0.09, risk_level="Medium") -> RiskAssessment:
    return RiskAssessment(
        risk_score=40.0, risk_level=risk_level, volatility_score=40.0, volatility_annualized=0.25,
        market_regime="Trending / Moderate Volatility", prediction_stability=90.0, confidence_penalty=2.0,
        expected_drawdown=expected_drawdown, expected_upside=expected_upside, method_notes="test",
    )


class TestBuildRecommendation:
    def test_no_prediction_returns_none(self):
        empty = PredictionResult(symbol="TEST.NS", generated_at=datetime.now(timezone.utc))
        assert build_recommendation(empty) is None

    def test_up_prediction_leans_up(self):
        result = _base_result(prob_up=0.75)
        rec = build_recommendation(result)
        assert rec.stance == "Leans Up"
        assert rec.stance_strength == result.confidence.confidence_level

    def test_down_prediction_leans_down(self):
        result = _base_result(prob_up=0.25)
        rec = build_recommendation(result)
        assert rec.stance == "Leans Down"

    def test_horizon_never_claims_a_multiday_holding_period(self):
        result = _base_result()
        rec = build_recommendation(result)
        assert "next trading session" in rec.horizon.lower()
        assert "single session" in rec.horizon.lower() or "one" in rec.horizon.lower() or "next trading session only" in rec.horizon.lower()

    def test_no_risk_means_no_reference_stop_level_and_says_so(self):
        result = _base_result()
        rec = build_recommendation(result)
        assert rec.reference_stop_level is None
        assert "unavailable" in rec.reference_stop_note.lower()
        assert any("no risk assessment" in r.lower() for r in rec.key_risks)

    def test_up_prediction_uses_expected_drawdown_as_reference_stop(self):
        result = _base_result(prob_up=0.8)
        result.risk = _fake_risk(expected_drawdown=-0.18, expected_upside=0.10)
        rec = build_recommendation(result)
        assert rec.reference_stop_level == pytest.approx(-0.18)
        assert "18" in rec.reference_stop_note  # the percentage appears in the note

    def test_down_prediction_uses_expected_upside_as_reference_stop(self):
        result = _base_result(prob_up=0.2)
        result.risk = _fake_risk(expected_drawdown=-0.10, expected_upside=0.22)
        rec = build_recommendation(result)
        assert rec.reference_stop_level == pytest.approx(0.22)

    def test_significant_drift_adds_a_key_risk(self):
        result = _base_result()
        result.risk = _fake_risk()
        result.drift_status = "Significant Drift"
        rec = build_recommendation(result)
        assert any("drift" in r.lower() for r in rec.key_risks)

    def test_stale_data_adds_a_key_risk(self):
        result = _base_result()
        result.risk = _fake_risk()
        result.data_freshness = "Stale"
        rec = build_recommendation(result)
        assert any("stale" in r.lower() for r in rec.key_risks)

    def test_low_confidence_adds_a_key_risk(self):
        result = _base_result(prob_up=0.51)  # tiny edge -> Very Low confidence
        result.risk = _fake_risk()
        rec = build_recommendation(result)
        assert result.confidence.confidence_level in ("Low", "Very Low")
        assert any("low" in r.lower() and "confidence" in r.lower() for r in rec.key_risks)

    def test_rationale_reflects_real_historical_performance_when_present(self):
        result = _base_result()
        result.historical_performance = PerformanceStats(n=25, accuracy=0.64, precision=0.6, recall=0.7, f1=0.65)
        rec = build_recommendation(result)
        assert "64%" in rec.rationale
        assert "25" in rec.rationale

    def test_rationale_does_not_fabricate_history_when_absent(self):
        result = _base_result()
        result.historical_performance = PerformanceStats(n=0, accuracy=None, precision=None, recall=None, f1=None)
        rec = build_recommendation(result)
        assert "no resolved historical track record" in rec.rationale.lower()

    def test_rationale_never_states_a_raw_probability_percentage(self):
        # Regression check for the Chances/Probability feature removal: the rationale
        # must never render confidence.probability_up as a "NN%" value, even though the
        # underlying probability_up still drives `stance` internally.
        for prob_up in (0.51, 0.7, 0.95):
            result = _base_result(prob_up=prob_up)
            rec = build_recommendation(result)
            assert "probability" not in rec.rationale.lower()
            assert re.search(r"\d{1,3}%", rec.rationale) is None

    def test_fallback_model_source_adds_a_caveat(self):
        result = _base_result()
        result.model_source = "in_app_fallback"
        rec = build_recommendation(result)
        assert any("fallback" in c.lower() for c in rec.caveats)

    def test_always_includes_the_not_financial_advice_caveat(self):
        result = _base_result()
        rec = build_recommendation(result)
        assert any("not financial advice" in c.lower() for c in rec.caveats)
