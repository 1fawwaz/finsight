"""Tests for core.ml.training: Optuna-tuned model comparison across CatBoost, XGBoost,
LightGBM, and RandomForest, with per-fold leakage-safe evaluation and full trial logging."""

import numpy as np
import pandas as pd
import pytest

from core.database import MLTrainingRun, get_session
from core.ml.cv import time_series_cv_folds
from core.ml.training import MODEL_FAMILIES, evaluate_params_on_folds, tune_model_family


def _make_panel(n_dates: int = 200, symbols: tuple[str, ...] = ("AAA.NS", "BBB.NS", "CCC.NS"), seed: int = 0) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-01", periods=n_dates).date
    index = pd.MultiIndex.from_product([symbols, dates], names=["symbol", "date"])
    n = len(index)
    features = pd.DataFrame(
        {
            "lag_return_1": rng.normal(0, 0.01, n),
            "rsi_14": rng.uniform(20, 80, n),
            "macd": rng.normal(0, 1, n),
            "volatility_20": rng.uniform(0.1, 0.5, n),
        },
        index=index,
    )
    labels = pd.Series(rng.integers(0, 2, n), index=index)
    return features, labels


@pytest.mark.parametrize("family", MODEL_FAMILIES)
def test_evaluate_params_on_folds_runs_for_every_model_family(family, temp_db):
    features, labels = _make_panel(200)
    folds = time_series_cv_folds(features, n_folds=2)
    default_params = {} if family != "random_forest" else {"n_estimators": 20, "max_depth": 3, "min_samples_leaf": 5, "max_features": "sqrt"}
    if family == "catboost":
        default_params = {"iterations": 20, "depth": 3, "learning_rate": 0.1, "l2_leaf_reg": 3.0}
    elif family == "xgboost":
        default_params = {"n_estimators": 20, "max_depth": 3, "learning_rate": 0.1, "subsample": 0.8, "colsample_bytree": 0.8, "reg_lambda": 1.0}
    elif family == "lightgbm":
        default_params = {"n_estimators": 20, "max_depth": 3, "learning_rate": 0.1, "num_leaves": 15, "min_child_samples": 5}

    result = evaluate_params_on_folds(family, default_params, features, labels, folds)

    assert result.family == family
    assert len(result.fold_metrics) == len(folds)
    for key in ("accuracy", "precision", "recall", "f1", "roc_auc"):
        assert key in result.mean_metrics
        assert key in result.std_metrics


def test_tune_model_family_logs_every_trial_not_just_the_best(temp_db):
    features, labels = _make_panel(200)
    best, all_trials = tune_model_family(
        "random_forest", features, labels, dataset_version="test_ds", feature_version="test_fs", n_trials=3, n_cv_folds=2
    )

    assert len(all_trials) == 3
    assert best in all_trials
    assert best.mean_metrics["roc_auc"] == max(t.mean_metrics["roc_auc"] for t in all_trials)

    with get_session() as session:
        logged = session.query(MLTrainingRun).filter(MLTrainingRun.model_family == "random_forest").all()
    assert len(logged) == 3
    assert {r.trial_number for r in logged} == {0, 1, 2}


def test_tune_model_family_is_deterministic_with_fixed_seed(temp_db):
    features, labels = _make_panel(200)
    best_1, _ = tune_model_family("random_forest", features, labels, "test_ds", "test_fs", n_trials=3, n_cv_folds=2)
    best_2, _ = tune_model_family("random_forest", features, labels, "test_ds", "test_fs", n_trials=3, n_cv_folds=2)
    assert best_1.params == best_2.params
    assert best_1.mean_metrics["roc_auc"] == pytest.approx(best_2.mean_metrics["roc_auc"])


def test_tune_model_family_rejects_unknown_family(temp_db):
    features, labels = _make_panel(200)
    with pytest.raises(ValueError, match="Unknown model family"):
        tune_model_family("not_a_real_model", features, labels, "test_ds", "test_fs", n_trials=1, n_cv_folds=2)
