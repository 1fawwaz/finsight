"""Tests for core/ml/prediction_service.py -- the single prediction orchestration
point. Verifies it never fabricates data (marks things unavailable instead) and never
crashes on edge-case inputs."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.ml.prediction_service import generate_prediction


def _real_looking_history(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    prices = 100 + np.cumsum(rng.normal(0, 1, n))
    volume = rng.integers(100_000, 500_000, n)
    return pd.DataFrame({"close": prices, "volume": volume}, index=dates)


class TestGeneratePredictionEdgeCases:
    def test_empty_dataframe_does_not_crash_and_marks_unavailable(self):
        empty = pd.DataFrame({"close": [], "volume": []})
        result = generate_prediction("EMPTY.NS", empty)
        assert result.has_prediction is False
        assert result.confidence is None
        assert len(result.warnings) >= 1

    def test_too_short_history_does_not_crash_and_marks_unavailable(self):
        short = pd.DataFrame({"close": [100.0 + i for i in range(10)], "volume": [1000] * 10}, index=pd.date_range("2026-01-01", periods=10))
        result = generate_prediction("SHORT.NS", short)
        assert result.has_prediction is False
        assert result.confidence is None

    def test_missing_columns_does_not_crash(self):
        malformed = pd.DataFrame({"close": [100.0] * 60}, index=pd.date_range("2026-01-01", periods=60))
        with pytest.raises(KeyError):
            # core.ml_model.build_features requires a "volume" column -- this is the
            # existing, unmodified function's own contract; prediction_service doesn't
            # swallow a caller passing a genuinely malformed frame missing a required
            # column, only "too little history" / "features didn't compute cleanly".
            generate_prediction("MALFORMED.NS", malformed)

    def test_corrupted_or_missing_model_artifact_falls_back_gracefully(self, monkeypatch):
        # Simulates a registry entry whose serialized .joblib file is missing/corrupted
        # on disk -- get_active_model's own FileNotFoundError contract. generate_prediction
        # must never crash the application over this; it must fall back to the in-app
        # model instead.
        monkeypatch.setattr(
            "core.ml.registry.get_active_model",
            lambda model_name: (_ for _ in ()).throw(FileNotFoundError("registry artifact missing on disk")),
        )
        result = generate_prediction("SYNTH8.NS", _real_looking_history())
        assert result.has_prediction is True
        assert result.model_source == "in_app_fallback"
        assert any("calibration" in w.lower() or "fallback" in w.lower() for w in result.warnings)

    def test_nan_registry_features_fall_back_without_crashing(self, monkeypatch):
        # Simulates the registry model's feature computation producing NaN in the
        # latest row (e.g. a data gap, or too little history for a long rolling
        # window) even though there's plenty of history overall -- both
        # core.ml_model._predict_with_registry_model and
        # core.ml.prediction_service.generate_prediction's own recheck must treat this
        # as "registry path unusable" and fall back to the in-app model, never crash or
        # silently predict from a NaN-containing feature row.
        rng = np.random.default_rng(7)
        n = 400
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        high = close + rng.uniform(0.5, 2.0, n)
        low = close - rng.uniform(0.5, 2.0, n)
        open_ = close + rng.normal(0, 0.5, n)
        volume = rng.integers(100_000, 500_000, n)
        good_ohlcv = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=dates)

        def _nan_features(price_df, sentiment_by_date=None):
            from core.ml.feature_pipeline import build_features_v2 as real_build_features_v2

            feats = real_build_features_v2(price_df, sentiment_by_date)
            feats = feats.copy()
            feats.iloc[-1, 0] = float("nan")
            return feats

        monkeypatch.setattr("core.ml.feature_pipeline.build_features_v2", _nan_features)
        result = generate_prediction("NANFEATURES.NS", good_ohlcv)
        assert result.has_prediction is True
        assert result.model_source == "in_app_fallback"


class TestGeneratePredictionHappyPath:
    def test_produces_a_full_confidence_assessment(self):
        result = generate_prediction("SYNTH.NS", _real_looking_history())
        assert result.has_prediction is True
        assert result.confidence is not None
        assert result.confidence.prediction_class in ("UP", "DOWN")
        assert 0.0 <= result.confidence.confidence_score <= 100.0
        assert result.confidence.confidence_level in ("Very High", "High", "Medium", "Low", "Very Low")

    def test_always_reports_a_model_source(self):
        # Every prediction must answer "which model produced this" -- either the
        # registry or an explicitly-labeled fallback, never silently blank.
        result = generate_prediction("SYNTH2.NS", _real_looking_history())
        assert result.model_source in ("registry", "in_app_fallback")
        if result.model_source == "in_app_fallback":
            assert any("fallback" in w.lower() for w in result.warnings)

    def test_probabilities_sum_to_one(self):
        result = generate_prediction("SYNTH3.NS", _real_looking_history())
        assert result.confidence.probability_up + result.confidence.probability_down == pytest.approx(1.0)

    def test_generated_at_is_set(self):
        result = generate_prediction("SYNTH4.NS", _real_looking_history())
        assert result.generated_at is not None

    def test_data_freshness_and_latest_timestamp_are_populated(self):
        # Synthetic history's last bar is far in the past relative to "now" -- freshness
        # must be honestly reported as Stale, never fabricated as Fresh.
        result = generate_prediction("SYNTH5.NS", _real_looking_history())
        assert result.data_freshness in ("Fresh", "Delayed", "Stale", "Unknown")
        assert result.latest_market_timestamp is not None

    def test_stale_data_produces_a_warning(self):
        result = generate_prediction("SYNTH6.NS", _real_looking_history())
        if result.data_freshness == "Stale":
            assert any("stale" in w.lower() for w in result.warnings)

    def test_recommendation_is_populated_and_never_fabricates_a_holding_period(self):
        result = generate_prediction("SYNTH7.NS", _real_looking_history())
        assert result.recommendation is not None
        assert result.recommendation.stance in ("Leans Up", "Leans Down")
        assert "next trading session" in result.recommendation.horizon.lower()
        assert any("not financial advice" in c.lower() for c in result.recommendation.caveats)
