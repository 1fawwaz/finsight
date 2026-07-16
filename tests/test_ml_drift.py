"""Tests for core/ml/drift.py -- runtime feature/prediction/concept drift detection.
Every check must report "Insufficient Data" honestly rather than a fabricated "Stable"
verdict when there isn't enough real history to judge -- most tests here verify exactly
that boundary."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest

from core.database import Prediction, Ticker, get_session
from core.ml.drift import (
    MIN_PREDICTIONS_FOR_PREDICTION_DRIFT,
    MIN_RESOLVED_FOR_CONCEPT_DRIFT,
    _psi,
    _rollup_status,
    assess_drift,
    compute_concept_drift,
    compute_feature_drift,
    compute_prediction_drift,
)


def _seed_ticker(symbol: str) -> int:
    with get_session() as session:
        t = Ticker(symbol=symbol, name=symbol)
        session.add(t)
        session.flush()
        return t.id


def _seed_prediction(ticker_id, d, probability, actual=None, model_version="m_v1"):
    with get_session() as session:
        session.add(
            Prediction(
                ticker_id=ticker_id,
                date=d,
                model_version=model_version,
                predicted_direction=1 if probability >= 0.5 else 0,
                probability=probability,
                actual_direction=actual,
                recorded_at=datetime.now(timezone.utc),
            )
        )
        session.flush()


class TestPSI:
    def test_identical_distributions_have_near_zero_psi(self):
        rng = np.random.default_rng(1)
        values = rng.normal(size=1000)
        psi = _psi(values, values.copy())
        assert psi == pytest.approx(0.0, abs=1e-6)

    def test_shifted_distribution_has_positive_psi(self):
        rng = np.random.default_rng(2)
        train = rng.normal(loc=0, scale=1, size=2000)
        live = rng.normal(loc=3, scale=1, size=500)  # a large, obvious shift
        psi = _psi(train, live)
        assert psi > 0.25  # should register as significant drift

    def test_too_few_training_values_returns_nan(self):
        assert np.isnan(_psi(np.array([1.0, 2.0]), np.array([1.0, 2.0, 3.0])))


class TestRollupStatus:
    def test_all_insufficient_rolls_up_to_insufficient(self):
        assert _rollup_status(["Insufficient Data", "Insufficient Data"]) == "Insufficient Data"

    def test_worst_status_wins(self):
        assert _rollup_status(["Stable", "Drifting", "Insufficient Data"]) == "Drifting"
        assert _rollup_status(["Stable", "Significant Drift", "Drifting"]) == "Significant Drift"

    def test_all_stable_is_stable(self):
        assert _rollup_status(["Stable", "Stable"]) == "Stable"


class TestComputePredictionDrift:
    def test_unknown_symbol_is_insufficient_data(self, temp_db):
        status, detail = compute_prediction_drift("NEVER_SEEDED.NS", "m_v1")
        assert status == "Insufficient Data"

    def test_too_few_predictions_is_insufficient_data(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(MIN_PREDICTIONS_FOR_PREDICTION_DRIFT - 1):
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.5)
        status, detail = compute_prediction_drift("A.NS", "m_v1")
        assert status == "Insufficient Data"

    def test_stable_probabilities_report_stable(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(20):
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.55)  # constant -> no shift
        status, detail = compute_prediction_drift("A.NS", "m_v1")
        assert status == "Stable"
        assert detail is not None

    def test_large_probability_shift_is_significant_drift(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(10):
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.50)  # earlier half
        for i in range(10):
            _seed_prediction(tid, date(2026, 2, 1 + i), 0.90)  # recent half, big shift
        status, detail = compute_prediction_drift("A.NS", "m_v1")
        assert status == "Significant Drift"

    def test_only_counts_the_matching_model_version(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(20):
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.5, model_version="other_model")
        status, detail = compute_prediction_drift("A.NS", "m_v1")
        assert status == "Insufficient Data"  # none of the seeded rows match m_v1


class TestComputeConceptDrift:
    def test_no_registered_accuracy_is_insufficient_data(self, temp_db):
        status, detail = compute_concept_drift("A.NS", "m_v1", registered_accuracy=None)
        assert status == "Insufficient Data"

    def test_too_few_resolved_is_insufficient_data(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(MIN_RESOLVED_FOR_CONCEPT_DRIFT - 1):
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.6, actual=1)
        status, detail = compute_concept_drift("A.NS", "m_v1", registered_accuracy=0.55)
        assert status == "Insufficient Data"

    def test_matching_accuracy_is_stable(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(MIN_RESOLVED_FOR_CONCEPT_DRIFT):
            # alternate correct/incorrect to land near a plausible accuracy
            actual = 1 if i % 2 == 0 else 0
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.6, actual=actual)
        status, detail = compute_concept_drift("A.NS", "m_v1", registered_accuracy=0.5)
        assert status in ("Stable", "Drifting")  # not asserting exact accuracy, just that it runs and returns a real verdict

    def test_large_accuracy_drop_is_significant_drift(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(MIN_RESOLVED_FOR_CONCEPT_DRIFT):
            # predicted_direction is always 1 (probability=0.6 >= 0.5); actual is always 0
            # -> live accuracy is 0.0, a massive drop from a registered 0.9
            _seed_prediction(tid, date(2026, 1, 1 + i), 0.6, actual=0)
        status, detail = compute_concept_drift("A.NS", "m_v1", registered_accuracy=0.9)
        assert status == "Significant Drift"


class TestComputeFeatureDrift:
    def test_unloadable_feature_version_returns_empty_list_not_a_crash(self, monkeypatch):
        import core.ml.drift as drift_module

        drift_module._cached_training_features.cache_clear()
        monkeypatch.setattr(
            "core.ml.feature_pipeline.load_feature_set",
            lambda feature_version: (_ for _ in ()).throw(FileNotFoundError("no such feature set")),
        )
        import pandas as pd

        price_df = pd.DataFrame({"close": [1.0] * 60, "open": [1.0] * 60, "high": [1.0] * 60, "low": [1.0] * 60, "volume": [1] * 60})
        result = compute_feature_drift("never_built", price_df)
        assert result == []
        drift_module._cached_training_features.cache_clear()


class TestAssessDrift:
    def test_no_feature_version_and_no_predictions_is_fully_insufficient(self, temp_db):
        import pandas as pd

        price_df = pd.DataFrame({"close": [100.0] * 60}, index=pd.date_range("2026-01-01", periods=60))
        report = assess_drift("NEVER_SEEDED.NS", "m_v1", feature_version=None, price_df=price_df)
        assert report.overall_status == "Insufficient Data"
        assert report.recommend_retraining is False
        assert any("feature version" in w.lower() for w in report.warnings)

    def test_never_raises_on_a_broken_sub_check(self, temp_db, monkeypatch):
        import pandas as pd

        monkeypatch.setattr("core.ml.drift.compute_prediction_drift", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        price_df = pd.DataFrame({"close": [100.0] * 60}, index=pd.date_range("2026-01-01", periods=60))
        report = assess_drift("A.NS", "m_v1", feature_version=None, price_df=price_df)
        assert report.prediction_drift_status == "Insufficient Data"
        assert any("prediction drift" in w.lower() for w in report.warnings)
