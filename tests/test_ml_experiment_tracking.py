"""Tests for core.ml.experiment_tracking: Phase 2 Step 11."""

import json

from core.database import MLTrainingRun, get_session
from core.ml.experiment_tracking import get_experiment, get_experiment_history, log_experiment


def test_log_experiment_persists_all_fields(temp_db):
    run = log_experiment(
        model_family="random_forest",
        dataset_version="dataset_v1",
        feature_version="features_v1",
        hyperparameters={"n_estimators": 100},
        metrics={"accuracy": 0.51, "roc_auc": 0.52},
        fold_metrics=[{"fold": 1, "accuracy": 0.51}],
        training_duration_seconds=12.5,
        prediction_latency_ms=3.2,
        calibration_results={"method": "platt", "ece": 0.04},
        feature_importance={"rsi_14": 0.05},
        notes="test experiment",
    )

    assert run.id is not None
    assert run.model_family == "random_forest"
    assert run.training_duration_seconds == 12.5
    assert run.prediction_latency_ms == 3.2
    assert json.loads(run.calibration_results_json) == {"method": "platt", "ece": 0.04}
    assert json.loads(run.feature_importance_json) == {"rsi_14": 0.05}
    assert run.notes == "test experiment"


def test_log_experiment_captures_git_commit_hash_when_available(temp_db):
    run = log_experiment(
        model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1",
        hyperparameters={}, metrics={"accuracy": 0.5},
    )
    # This repo IS a real git repo -- a real commit hash should be captured, not None.
    assert run.git_commit_hash is not None
    assert len(run.git_commit_hash) == 40  # a real SHA-1 hex string


def test_log_experiment_optional_fields_default_to_none(temp_db):
    run = log_experiment(
        model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1",
        hyperparameters={}, metrics={"accuracy": 0.5},
    )
    assert run.training_duration_seconds is None
    assert run.calibration_results_json is None
    assert run.notes is None


def test_each_log_experiment_call_creates_a_new_immutable_row(temp_db):
    first = log_experiment(
        model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1",
        hyperparameters={}, metrics={"accuracy": 0.5},
    )
    second = log_experiment(
        model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1",
        hyperparameters={}, metrics={"accuracy": 0.6},
    )

    assert first.id != second.id
    with get_session() as session:
        assert session.query(MLTrainingRun).count() == 2


def test_no_update_experiment_function_exists():
    """Structural immutability check: this module's public surface has no function
    that could modify a historical experiment row."""
    import core.ml.experiment_tracking as module

    public_names = [name for name in dir(module) if not name.startswith("_")]
    assert not any("update" in name.lower() or "delete" in name.lower() or "overwrite" in name.lower() for name in public_names)


def test_get_experiment_retrieves_by_id(temp_db):
    logged = log_experiment(
        model_family="xgboost", dataset_version="dataset_v1", feature_version="features_v1",
        hyperparameters={"max_depth": 3}, metrics={"accuracy": 0.5},
    )

    with get_session() as session:
        fetched = get_experiment(session, logged.id)

    assert fetched.id == logged.id
    assert fetched.model_family == "xgboost"


def test_get_experiment_returns_none_for_unknown_id(temp_db):
    with get_session() as session:
        assert get_experiment(session, 99999) is None


def test_get_experiment_history_filters_by_model_family(temp_db):
    log_experiment(model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1", hyperparameters={}, metrics={"accuracy": 0.5})
    log_experiment(model_family="xgboost", dataset_version="dataset_v1", feature_version="features_v1", hyperparameters={}, metrics={"accuracy": 0.5})

    with get_session() as session:
        history = get_experiment_history(session, model_family="xgboost")

    assert len(history) == 1
    assert history.iloc[0]["model_family"] == "xgboost"


def test_get_experiment_history_orders_newest_first(temp_db):
    first = log_experiment(model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1", hyperparameters={}, metrics={"accuracy": 0.1})
    second = log_experiment(model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1", hyperparameters={}, metrics={"accuracy": 0.2})

    with get_session() as session:
        history = get_experiment_history(session)

    assert history.iloc[0]["id"] == second.id
    assert history.iloc[-1]["id"] == first.id


def test_get_experiment_history_empty_when_nothing_logged(temp_db):
    with get_session() as session:
        history = get_experiment_history(session, model_family="never_logged")
    assert history.empty


def test_get_experiment_history_deserializes_json_fields(temp_db):
    log_experiment(
        model_family="random_forest", dataset_version="dataset_v1", feature_version="features_v1",
        hyperparameters={"max_depth": 5}, metrics={"accuracy": 0.5},
        calibration_results={"ece": 0.03}, feature_importance={"rsi_14": 0.1},
    )

    with get_session() as session:
        history = get_experiment_history(session)

    row = history.iloc[0]
    assert row["hyperparameters"] == {"max_depth": 5}
    assert row["calibration_results"] == {"ece": 0.03}
    assert row["feature_importance"] == {"rsi_14": 0.1}
