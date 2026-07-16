"""Tests for core/ml/system_health.py -- AI Dashboard health checks."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from core.ml.prediction_service import PredictionResult
from core.ml.system_health import (
    check_active_model,
    check_database_connectivity,
    check_prediction_generated,
    check_price_data,
    run_all_checks,
)


class TestCheckDatabaseConnectivity:
    def test_real_db_connection_is_ok(self, temp_db):
        result = check_database_connectivity()
        assert result.ok is True


class _HealthCheckModel:
    """Module-level (not nested) so joblib can pickle it in TestCheckActiveModel."""


class TestCheckActiveModel:
    def test_unregistered_model_name_is_not_ok(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        result = check_active_model("never_registered_model")
        assert result.ok is False

    def test_registered_active_model_is_ok(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module
        from core.ml.registry import register_model

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)

        register_model(_HealthCheckModel(), "health_check_model", "xgboost", "ds1", "fs1", {}, {}, activate=True)
        result = check_active_model("health_check_model")
        assert result.ok is True
        assert "health_check_model_v1" in result.detail


class TestCheckPriceData:
    def test_empty_dataframe_is_not_ok(self):
        result = check_price_data(pd.DataFrame())
        assert result.ok is False

    def test_none_is_not_ok(self):
        result = check_price_data(None)
        assert result.ok is False

    def test_populated_dataframe_is_ok(self):
        result = check_price_data(pd.DataFrame({"close": [100.0, 101.0]}))
        assert result.ok is True
        assert "2" in result.detail


class TestCheckPredictionGenerated:
    def test_no_prediction_is_not_ok(self):
        empty = PredictionResult(symbol="TEST.NS", generated_at=datetime.now(timezone.utc))
        result = check_prediction_generated(empty)
        assert result.ok is False

    def test_none_result_is_not_ok(self):
        result = check_prediction_generated(None)
        assert result.ok is False


class TestRunAllChecks:
    def test_returns_four_checks_and_never_raises(self, temp_db, tmp_path, monkeypatch):
        import core.ml.registry as registry_module

        monkeypatch.setattr(registry_module, "MODEL_ARTIFACT_DIR", tmp_path)
        checks = run_all_checks("never_registered", pd.DataFrame(), None)
        assert len(checks) == 4
        assert all(hasattr(c, "ok") for c in checks)
