"""Tests for core.ml.feature_importance_monitoring: Phase 2 Step 10."""

import pandas as pd
import pytest

from core.database import FeatureImportanceSnapshot
from core.ml.feature_importance_monitoring import (
    detect_all_drift,
    detect_feature_drift,
    get_importance_history,
    record_importance_snapshot,
)


def test_record_importance_snapshot_persists_every_feature(db_session):
    count = record_importance_snapshot(db_session, "exp_1", "permutation", {"rsi_14": 0.05, "macd": 0.03})
    assert count == 2
    assert db_session.query(FeatureImportanceSnapshot).count() == 2


def test_record_importance_snapshot_rejects_unknown_importance_type(db_session):
    with pytest.raises(ValueError, match="not in"):
        record_importance_snapshot(db_session, "exp_1", "made_up_type", {"rsi_14": 0.05})


def test_record_importance_snapshot_accepts_pandas_series(db_session):
    series = pd.Series({"rsi_14": 0.05, "macd": 0.03})
    count = record_importance_snapshot(db_session, "exp_1", "shap", series)
    assert count == 2


def test_get_importance_history_orders_by_time(db_session):
    record_importance_snapshot(db_session, "exp_1", "permutation", {"rsi_14": 0.05})
    record_importance_snapshot(db_session, "exp_2", "permutation", {"rsi_14": 0.08})
    record_importance_snapshot(db_session, "exp_3", "permutation", {"rsi_14": 0.02})

    history = get_importance_history(db_session, "rsi_14", "permutation")

    assert list(history["experiment_id"]) == ["exp_1", "exp_2", "exp_3"]
    assert list(history["value"]) == [0.05, 0.08, 0.02]


def test_get_importance_history_empty_when_never_recorded(db_session):
    history = get_importance_history(db_session, "never_recorded", "permutation")
    assert history.empty


def test_detect_feature_drift_none_with_fewer_than_two_snapshots(db_session):
    record_importance_snapshot(db_session, "exp_1", "permutation", {"rsi_14": 0.05})
    assert detect_feature_drift(db_session, "rsi_14", "permutation") is None


def test_detect_feature_drift_flags_significant_change(db_session):
    record_importance_snapshot(db_session, "exp_1", "permutation", {"rsi_14": 0.10})
    record_importance_snapshot(db_session, "exp_2", "permutation", {"rsi_14": 0.02})  # -80% change

    alert = detect_feature_drift(db_session, "rsi_14", "permutation", drift_threshold=0.5)

    assert alert.significant is True
    assert alert.previous_value == 0.10
    assert alert.current_value == 0.02
    assert alert.relative_change == pytest.approx(-0.8)


def test_detect_feature_drift_does_not_flag_small_change(db_session):
    record_importance_snapshot(db_session, "exp_1", "permutation", {"rsi_14": 0.10})
    record_importance_snapshot(db_session, "exp_2", "permutation", {"rsi_14": 0.11})  # +10%

    alert = detect_feature_drift(db_session, "rsi_14", "permutation", drift_threshold=0.5)

    assert alert.significant is False


def test_detect_feature_drift_handles_zero_baseline_without_crashing(db_session):
    record_importance_snapshot(db_session, "exp_1", "permutation", {"new_feature": 0.0})
    record_importance_snapshot(db_session, "exp_2", "permutation", {"new_feature": 0.05})

    alert = detect_feature_drift(db_session, "new_feature", "permutation")

    assert alert.significant is True
    assert alert.relative_change == float("inf")


def test_detect_all_drift_covers_every_feature_with_history(db_session):
    record_importance_snapshot(db_session, "exp_1", "permutation", {"a": 0.10, "b": 0.05})
    record_importance_snapshot(db_session, "exp_2", "permutation", {"a": 0.01, "b": 0.051})  # a: big drop, b: stable

    alerts = detect_all_drift(db_session, "permutation", drift_threshold=0.5)

    alert_by_feature = {a.feature_name: a for a in alerts}
    assert alert_by_feature["a"].significant is True
    assert alert_by_feature["b"].significant is False


def test_importance_types_are_independent_per_feature(db_session):
    """The same feature can have both permutation and SHAP snapshots -- they must not
    be mixed together in history or drift detection."""
    record_importance_snapshot(db_session, "exp_1", "permutation", {"rsi_14": 0.10})
    record_importance_snapshot(db_session, "exp_1", "shap", {"rsi_14": 0.50})

    perm_history = get_importance_history(db_session, "rsi_14", "permutation")
    shap_history = get_importance_history(db_session, "rsi_14", "shap")

    assert list(perm_history["value"]) == [0.10]
    assert list(shap_history["value"]) == [0.50]
