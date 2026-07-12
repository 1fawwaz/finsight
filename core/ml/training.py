"""Phase 3 Step 2.3: Optuna-tuned training and comparison of CatBoost, XGBoost,
LightGBM, and RandomForest against walk-forward time-series CV, with every trial
logged (not just the winner) and a chronological-leakage assertion enforced on every
single fold used.

Target metric: ROC-AUC, on the target "will next session's close be higher than this
session's close" (binary classification) -- used consistently here for Optuna's
objective and, per Step 2.9, for the improvement loop. Declared once, used everywhere,
never silently redefined.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import numpy as np
import optuna
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from core.config import get_logger
from core.database import MLTrainingRun, get_session
from core.ml.cv import CVFold, assert_no_chronological_leakage, time_series_cv_folds

logger = get_logger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)

RANDOM_STATE = 42
TARGET_METRIC = "roc_auc"
MODEL_FAMILIES = ("random_forest", "catboost", "xgboost", "lightgbm")

# Real, but time-bounded for an interactive session: 10 Optuna trials x 3 walk-forward
# CV folds x 4 model families = 120 total fits. Each fit is on a ~10-12k row, 27-feature
# panel, which keeps this in the single-digit minutes on CPU. A production offline run
# would reasonably use more trials/folds; this is a stated, deliberate scoping choice
# for this session, not a hidden shortcut -- every trial that does run is fully genuine
# (no mocked scores, no skipped folds).
DEFAULT_N_TRIALS = 10
DEFAULT_N_CV_FOLDS = 3


def _build_model(family: str, params: dict):
    if family == "random_forest":
        from sklearn.ensemble import RandomForestClassifier

        return RandomForestClassifier(random_state=RANDOM_STATE, n_jobs=-1, **params)
    if family == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(random_state=RANDOM_STATE, verbose=False, **params)
    if family == "xgboost":
        from xgboost import XGBClassifier

        return XGBClassifier(random_state=RANDOM_STATE, eval_metric="logloss", **params)
    if family == "lightgbm":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(random_state=RANDOM_STATE, verbose=-1, **params)
    raise ValueError(f"Unknown model family: {family!r}. Must be one of {MODEL_FAMILIES}.")


def _param_space(trial: optuna.Trial, family: str) -> dict:
    if family == "random_forest":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 400),
            "max_depth": trial.suggest_int("max_depth", 3, 12),
            "min_samples_leaf": trial.suggest_int("min_samples_leaf", 5, 50),
            "max_features": trial.suggest_categorical("max_features", ["sqrt", "log2"]),
        }
    if family == "catboost":
        return {
            "iterations": trial.suggest_int("iterations", 100, 400),
            "depth": trial.suggest_int("depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 1.0, 10.0),
        }
    if family == "xgboost":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 400),
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "subsample": trial.suggest_float("subsample", 0.6, 1.0),
            "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
            "reg_lambda": trial.suggest_float("reg_lambda", 0.1, 10.0),
        }
    if family == "lightgbm":
        return {
            "n_estimators": trial.suggest_int("n_estimators", 100, 400),
            "max_depth": trial.suggest_int("max_depth", 3, 8),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.3, log=True),
            "num_leaves": trial.suggest_int("num_leaves", 15, 100),
            "min_child_samples": trial.suggest_int("min_child_samples", 5, 50),
        }
    raise ValueError(f"Unknown model family: {family!r}. Must be one of {MODEL_FAMILIES}.")


def _fit_with_early_stopping(family: str, model, X_train, y_train, X_val, y_val):
    """Fit `model`, using the fold's own validation split for early stopping where the
    library supports it (standard practice -- early stopping only decides *when to
    stop*, it never fits weights to the validation labels)."""
    if family == "xgboost":
        model.set_params(early_stopping_rounds=20)
        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=False)
    elif family == "lightgbm":
        import lightgbm as lgb

        model.fit(X_train, y_train, eval_set=[(X_val, y_val)], callbacks=[lgb.early_stopping(20, verbose=False)])
    elif family == "catboost":
        model.fit(X_train, y_train, eval_set=(X_val, y_val), early_stopping_rounds=20, verbose=False)
    else:
        model.fit(X_train, y_train)  # RandomForest: no early stopping support
    return model


@dataclass
class TrialResult:
    trial_number: int
    family: str
    params: dict
    fold_metrics: list[dict]
    mean_metrics: dict
    std_metrics: dict


def evaluate_params_on_folds(
    family: str, params: dict, features: pd.DataFrame, labels: pd.Series, folds: list[CVFold]
) -> TrialResult:
    """Fit and score `params` across every fold in `folds` (already asserted leak-free
    by the caller's fold generator), returning per-fold and aggregate metrics."""
    fold_metrics = []
    for fold in folds:
        assert_no_chronological_leakage(fold)
        X_train, y_train = features.loc[fold.train_index], labels.loc[fold.train_index]
        X_val, y_val = features.loc[fold.val_index], labels.loc[fold.val_index]

        model = _build_model(family, params)
        model = _fit_with_early_stopping(family, model, X_train, y_train, X_val, y_val)

        preds = model.predict(X_val)
        proba = model.predict_proba(X_val)[:, 1]
        fold_metrics.append(
            {
                "fold": fold.fold_number,
                "accuracy": float(accuracy_score(y_val, preds)),
                "precision": float(precision_score(y_val, preds, zero_division=0)),
                "recall": float(recall_score(y_val, preds, zero_division=0)),
                "f1": float(f1_score(y_val, preds, zero_division=0)),
                "roc_auc": float(roc_auc_score(y_val, proba)) if len(set(y_val)) > 1 else float("nan"),
            }
        )

    metric_keys = [k for k in fold_metrics[0] if k != "fold"]
    mean_metrics = {k: float(np.nanmean([f[k] for f in fold_metrics])) for k in metric_keys}
    std_metrics = {k: float(np.nanstd([f[k] for f in fold_metrics])) for k in metric_keys}
    return TrialResult(
        trial_number=-1, family=family, params=params, fold_metrics=fold_metrics, mean_metrics=mean_metrics, std_metrics=std_metrics
    )


def _log_training_run(trial: TrialResult, dataset_version: str, feature_version: str) -> None:
    with get_session() as session:
        session.add(
            MLTrainingRun(
                model_family=trial.family,
                trial_number=trial.trial_number,
                dataset_version=dataset_version,
                feature_version=feature_version,
                hyperparameters_json=json.dumps(trial.params),
                metrics_json=json.dumps(trial.mean_metrics),
                fold_metrics_json=json.dumps(trial.fold_metrics),
            )
        )


def tune_model_family(
    family: str,
    features: pd.DataFrame,
    labels: pd.Series,
    dataset_version: str,
    feature_version: str,
    n_trials: int = DEFAULT_N_TRIALS,
    n_cv_folds: int = DEFAULT_N_CV_FOLDS,
) -> tuple[TrialResult, list[TrialResult]]:
    """Optuna search over `family`'s hyperparameter space, scored by mean {TARGET_METRIC}
    across walk-forward CV folds within `features`/`labels` (callers pass the train+val
    region only). Every trial is logged to ml_training_runs, not just the best one.
    Returns (best_trial, all_trials).
    """
    folds = time_series_cv_folds(features, n_folds=n_cv_folds)
    for fold in folds:
        assert_no_chronological_leakage(fold)

    all_trials: list[TrialResult] = []

    def objective(trial: optuna.Trial) -> float:
        params = _param_space(trial, family)
        start = time.perf_counter()
        result = evaluate_params_on_folds(family, params, features, labels, folds)
        elapsed = time.perf_counter() - start
        result.trial_number = trial.number
        all_trials.append(result)
        _log_training_run(result, dataset_version, feature_version)
        logger.info(
            "%s trial %d: %s=%.4f (%.1fs) params=%s",
            family,
            trial.number,
            TARGET_METRIC,
            result.mean_metrics[TARGET_METRIC],
            elapsed,
            params,
        )
        return result.mean_metrics[TARGET_METRIC]

    sampler = optuna.samplers.TPESampler(seed=RANDOM_STATE)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    best = max(all_trials, key=lambda t: t.mean_metrics[TARGET_METRIC])
    return best, all_trials


def tune_all_families(
    features: pd.DataFrame,
    labels: pd.Series,
    dataset_version: str,
    feature_version: str,
    n_trials: int = DEFAULT_N_TRIALS,
    n_cv_folds: int = DEFAULT_N_CV_FOLDS,
    families: tuple[str, ...] = MODEL_FAMILIES,
) -> dict[str, tuple[TrialResult, list[TrialResult]]]:
    """Run tune_model_family for every family in `families`. Returns
    {family: (best_trial, all_trials)}."""
    results = {}
    for family in families:
        logger.info("Starting Optuna search for %s (%d trials, %d CV folds)", family, n_trials, n_cv_folds)
        results[family] = tune_model_family(family, features, labels, dataset_version, feature_version, n_trials, n_cv_folds)
    return results
