"""Phase 3 Step 2.3.1: corrective actions for a model flagged by the generalization
gate. Three genuinely different strategies per flagged model, in order:

1. Direct regularization/capacity adjustment (tighten hyperparameters for an overfit
   model, loosen them for an underfit one) -- the most direct lever.
2. Feature selection (drop the least label-correlated features) -- reduces noise
   dimensionality, which can help both an overfit model (fewer spurious splits) and an
   underfit one (less noise diluting what signal exists).
3. A narrowed Optuna re-tune, biased toward the direction implied by the flag, scored
   directly against the validation split (not the CV-fold mean) -- the spec's own
   "Optuna re-tuning against the validation metric" strategy.

Each attempt is scored with the same evaluate_generalization() used for the original
gate, so "did this actually fix it" is answered with the same evidence, not a proxy.
"""

from __future__ import annotations

from dataclasses import dataclass

import optuna
import pandas as pd
from sklearn.metrics import roc_auc_score

from core.config import get_logger
from core.ml.cv import CVFold, assert_no_chronological_leakage
from core.ml.generalization import GateResult, evaluate_generalization
from core.ml.training import RANDOM_STATE, TARGET_METRIC, build_model, fit_with_early_stopping

logger = get_logger(__name__)

optuna.logging.set_verbosity(optuna.logging.WARNING)


def _regularize(family: str, params: dict) -> dict:
    """Tighter capacity: shallower trees, larger leaf/child minimums, stronger L2."""
    p = dict(params)
    if family == "random_forest":
        p["max_depth"] = min(p.get("max_depth", 6), 3)
        p["min_samples_leaf"] = max(p.get("min_samples_leaf", 10), 50)
    elif family == "xgboost":
        p["max_depth"] = min(p.get("max_depth", 6), 3)
        p["reg_lambda"] = max(p.get("reg_lambda", 1.0) * 3, 10.0)
        p["subsample"] = min(p.get("subsample", 0.8), 0.6)
    elif family == "catboost":
        p["depth"] = min(p.get("depth", 6), 3)
        p["l2_leaf_reg"] = max(p.get("l2_leaf_reg", 3.0) * 3, 15.0)
    elif family == "lightgbm":
        p["max_depth"] = min(p.get("max_depth", 6), 3)
        p["min_child_samples"] = max(p.get("min_child_samples", 10), 80)
        p["num_leaves"] = min(p.get("num_leaves", 31), 15)
    return p


def _deregularize(family: str, params: dict) -> dict:
    """More capacity: deeper trees, smaller leaf/child minimums, weaker L2 -- the
    opposite lever, for an underfit model."""
    p = dict(params)
    if family == "random_forest":
        p["max_depth"] = max(p.get("max_depth", 6), 14)
        p["min_samples_leaf"] = min(p.get("min_samples_leaf", 10), 3)
    elif family == "xgboost":
        p["max_depth"] = max(p.get("max_depth", 6), 10)
        p["reg_lambda"] = min(p.get("reg_lambda", 1.0) / 3, 0.3)
    elif family == "catboost":
        p["depth"] = max(p.get("depth", 6), 9)
        p["l2_leaf_reg"] = min(p.get("l2_leaf_reg", 3.0) / 3, 1.0)
    elif family == "lightgbm":
        p["max_depth"] = max(p.get("max_depth", 6), 10)
        p["num_leaves"] = max(p.get("num_leaves", 31), 150)
    return p


def select_features_by_correlation(features: pd.DataFrame, labels: pd.Series, keep_fraction: float = 0.6) -> list[str]:
    """Keep the top `keep_fraction` of features by |correlation| with the label --
    reduces noise dimensionality for both an overfit model (fewer spurious splits to
    find) and an underfit one (less noise diluting weak real signal)."""
    label_float = labels.astype(float)
    correlations = {col: abs(features[col].corr(label_float)) for col in features.columns}
    correlations = {k: v for k, v in correlations.items() if pd.notna(v)}
    ranked = sorted(correlations, key=correlations.get, reverse=True)
    n_keep = max(1, int(len(ranked) * keep_fraction))
    return ranked[:n_keep]


def _retune_against_validation(
    family: str, base_params: dict, direction: str, train_X: pd.DataFrame, train_y: pd.Series, val_X: pd.DataFrame, val_y: pd.Series, n_trials: int = 8
) -> dict:
    """Optuna re-tune scored directly against the validation split, with the search
    space narrowed and biased toward `direction` ("regularize" or "deregularize")."""

    def objective(trial: optuna.Trial) -> float:
        if family == "random_forest":
            if direction == "regularize":
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 200),
                    "max_depth": trial.suggest_int("max_depth", 2, 4),
                    "min_samples_leaf": trial.suggest_int("min_samples_leaf", 40, 100),
                    "max_features": "sqrt",
                }
            else:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 200, 500),
                    "max_depth": trial.suggest_int("max_depth", 10, 20),
                    "min_samples_leaf": trial.suggest_int("min_samples_leaf", 2, 5),
                    "max_features": "sqrt",
                }
        elif family == "xgboost":
            if direction == "regularize":
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 150),
                    "max_depth": trial.suggest_int("max_depth", 2, 3),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.05, log=True),
                    "subsample": trial.suggest_float("subsample", 0.5, 0.7),
                    "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 0.7),
                    "reg_lambda": trial.suggest_float("reg_lambda", 5.0, 20.0),
                }
            else:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 200, 500),
                    "max_depth": trial.suggest_int("max_depth", 8, 12),
                    "learning_rate": trial.suggest_float("learning_rate", 0.05, 0.3, log=True),
                    "subsample": trial.suggest_float("subsample", 0.8, 1.0),
                    "colsample_bytree": trial.suggest_float("colsample_bytree", 0.8, 1.0),
                    "reg_lambda": trial.suggest_float("reg_lambda", 0.01, 0.3),
                }
        elif family == "catboost":
            if direction == "regularize":
                params = {
                    "iterations": trial.suggest_int("iterations", 50, 150),
                    "depth": trial.suggest_int("depth", 2, 3),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.05, log=True),
                    "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 10.0, 30.0),
                }
            else:
                params = {
                    "iterations": trial.suggest_int("iterations", 200, 500),
                    "depth": trial.suggest_int("depth", 7, 10),
                    "learning_rate": trial.suggest_float("learning_rate", 0.05, 0.3, log=True),
                    "l2_leaf_reg": trial.suggest_float("l2_leaf_reg", 0.1, 1.0),
                }
        elif family == "lightgbm":
            if direction == "regularize":
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 50, 150),
                    "max_depth": trial.suggest_int("max_depth", 2, 3),
                    "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.05, log=True),
                    "num_leaves": trial.suggest_int("num_leaves", 4, 15),
                    "min_child_samples": trial.suggest_int("min_child_samples", 60, 120),
                }
            else:
                params = {
                    "n_estimators": trial.suggest_int("n_estimators", 200, 500),
                    "max_depth": trial.suggest_int("max_depth", 8, 12),
                    "learning_rate": trial.suggest_float("learning_rate", 0.05, 0.3, log=True),
                    "num_leaves": trial.suggest_int("num_leaves", 100, 200),
                    "min_child_samples": trial.suggest_int("min_child_samples", 2, 10),
                }
        else:
            raise ValueError(f"Unknown family {family!r}")

        model = build_model(family, params)
        model = fit_with_early_stopping(family, model, train_X, train_y, val_X, val_y)
        proba = model.predict_proba(val_X)[:, 1]
        trial.set_user_attr("params", params)
        return roc_auc_score(val_y, proba)

    sampler = optuna.samplers.TPESampler(seed=RANDOM_STATE)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
    return study.best_trial.user_attrs["params"]


@dataclass
class CorrectionAttempt:
    attempt_number: int
    strategy: str
    params: dict
    feature_subset: list[str] | None
    result: GateResult


def attempt_corrections(
    family: str,
    original_result: GateResult,
    base_params: dict,
    fold_mean_metrics: dict,
    fold_std_metrics: dict,
    train_X: pd.DataFrame,
    train_y: pd.Series,
    val_X: pd.DataFrame,
    val_y: pd.Series,
    test_X: pd.DataFrame,
    test_y: pd.Series,
) -> list[CorrectionAttempt]:
    """Run up to 3 genuinely different corrective strategies for a flagged model,
    stopping early if one passes. Every attempt (pass or fail) is returned with its
    real evaluate_generalization() result -- never summarized away."""
    direction = "regularize" if (original_result.overfit_flag or original_result.instability_flag) else "deregularize"
    attempts: list[CorrectionAttempt] = []

    # Attempt 1: direct hyperparameter regularization/capacity adjustment.
    adjusted_params = _regularize(family, base_params) if direction == "regularize" else _deregularize(family, base_params)
    result_1 = evaluate_generalization(
        family, adjusted_params, fold_mean_metrics, fold_std_metrics, train_X, train_y, val_X, val_y, test_X, test_y
    )
    attempts.append(CorrectionAttempt(1, f"hyperparameter {direction}", adjusted_params, None, result_1))
    logger.info("Correction attempt 1 (%s) for %s: passed=%s", direction, family, result_1.passed)
    if result_1.passed:
        return attempts

    # Attempt 2: feature selection by label correlation (60% of features kept).
    kept_features = select_features_by_correlation(train_X, train_y, keep_fraction=0.6)
    result_2 = evaluate_generalization(
        family, adjusted_params, fold_mean_metrics, fold_std_metrics,
        train_X[kept_features], train_y, val_X[kept_features], val_y, test_X[kept_features], test_y,
    )
    attempts.append(CorrectionAttempt(2, "feature selection (top 60% by |correlation|)", adjusted_params, kept_features, result_2))
    logger.info("Correction attempt 2 (feature selection, %d/%d features) for %s: passed=%s", len(kept_features), train_X.shape[1], family, result_2.passed)
    if result_2.passed:
        return attempts

    # Attempt 3: Optuna re-tune scored directly against the validation split, search
    # space narrowed and biased toward the same direction as attempt 1.
    retuned_params = _retune_against_validation(family, base_params, direction, train_X, train_y, val_X, val_y)
    result_3 = evaluate_generalization(
        family, retuned_params, fold_mean_metrics, fold_std_metrics, train_X, train_y, val_X, val_y, test_X, test_y
    )
    attempts.append(CorrectionAttempt(3, f"Optuna re-tune vs validation ({direction}-biased)", retuned_params, None, result_3))
    logger.info("Correction attempt 3 (re-tune) for %s: passed=%s", family, result_3.passed)

    return attempts
