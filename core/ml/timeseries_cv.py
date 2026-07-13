"""Phase 2 Step 9: Time-Series Cross-Validation -- sklearn's own `TimeSeriesSplit`
(wired in directly, not reimplemented), rolling-origin one-step-ahead evaluation, and
nested CV (outer performance estimate / inner hyperparameter tuning, reusing
`core.ml.training.tune_model_family` for the inner loop rather than a second tuner).

Random or shuffled splits are never used anywhere in this module -- every splitter here
is chronological by construction (sklearn's `TimeSeriesSplit` never shuffles; the
rolling-origin and nested-CV loops below only ever slice forward in time).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score
from sklearn.model_selection import TimeSeriesSplit

from core.config import get_logger
from core.ml.cv import CVFold, assert_no_chronological_leakage, time_series_cv_folds
from core.ml.training import tune_model_family

logger = get_logger(__name__)

RANDOM_STATE = 42


def time_series_split_folds(features: pd.DataFrame, n_splits: int = 5, gap: int = 0) -> list[CVFold]:
    """sklearn's `TimeSeriesSplit`, wrapped to return this codebase's own `CVFold`
    shape (train_index/val_index as label-based `pd.Index`, not sklearn's raw integer
    positions) so it composes with `assert_no_chronological_leakage` and
    `core.ml.walk_forward.verify_no_leakage_report` exactly like the existing
    expanding/rolling fold generators -- one consistent fold representation across the
    whole ML foundation, not a second competing one.
    """
    splitter = TimeSeriesSplit(n_splits=n_splits, gap=gap)
    folds = []
    for i, (train_positions, val_positions) in enumerate(splitter.split(features), start=1):
        train_index = features.index[train_positions]
        val_index = features.index[val_positions]
        folds.append(
            CVFold(
                fold_number=i,
                train_index=train_index,
                val_index=val_index,
                train_date_range=(train_index.min(), train_index.max()),
                val_date_range=(val_index.min(), val_index.max()),
            )
        )
    return folds


@dataclass
class RollingOriginResult:
    origins: list
    predictions: pd.Series
    actuals: pd.Series
    accuracy: float
    precision: float
    recall: float


def rolling_origin_evaluation(
    features: pd.DataFrame, labels: pd.Series, min_train_size: int, step: int = 5,
) -> RollingOriginResult:
    """Classic rolling-origin evaluation: starting once `min_train_size` rows are
    available, train on everything up to origin t and predict *only* t+1 (strictly
    one-step-ahead, unlike Step 8's rolling/expanding folds which each predict a whole
    block of future rows) -- then advance the origin by `step` rows and repeat. `step`
    trades off evaluation granularity against cost (re-fitting a model at every single
    origin is the textbook definition but expensive over a multi-year daily series;
    `step` > 1 is a stated, deliberate approximation, not silently substituted for the
    real thing).
    """
    n = len(features)
    origins, predictions, actuals = [], [], []

    origin = min_train_size
    while origin + 1 <= n - 1:
        X_train, y_train = features.iloc[:origin], labels.iloc[:origin]
        X_next, y_next = features.iloc[[origin]], labels.iloc[origin]
        if y_train.nunique() < 2:
            origin += step
            continue

        model = RandomForestClassifier(n_estimators=100, max_depth=5, min_samples_leaf=10, random_state=RANDOM_STATE)
        model.fit(X_train, y_train)
        pred = int(model.predict(X_next)[0])

        origins.append(features.index[origin])
        predictions.append(pred)
        actuals.append(int(y_next))
        origin += step

    if not origins:
        raise ValueError(f"Not enough rows ({n}) for even one rolling-origin step at min_train_size={min_train_size}.")

    pred_series = pd.Series(predictions, index=pd.Index(origins, name="date"))
    actual_series = pd.Series(actuals, index=pd.Index(origins, name="date"))
    return RollingOriginResult(
        origins=origins,
        predictions=pred_series,
        actuals=actual_series,
        accuracy=float(accuracy_score(actual_series, pred_series)),
        precision=float(precision_score(actual_series, pred_series, zero_division=0)),
        recall=float(recall_score(actual_series, pred_series, zero_division=0)),
    )


@dataclass
class NestedCVFoldResult:
    outer_fold_number: int
    best_inner_params: dict
    inner_mean_metrics: dict
    outer_test_accuracy: float
    outer_test_precision: float
    outer_test_recall: float


@dataclass
class NestedCVReport:
    family: str
    fold_results: list[NestedCVFoldResult]

    @property
    def mean_outer_test_accuracy(self) -> float:
        return float(pd.Series([r.outer_test_accuracy for r in self.fold_results]).mean())


def nested_time_series_cv(
    features: pd.DataFrame, labels: pd.Series, family: str = "random_forest",
    n_outer_folds: int = 3, n_inner_trials: int = 5, n_inner_folds: int = 3,
) -> NestedCVReport:
    """Nested CV: for each OUTER fold (an honest, held-out performance estimate), tune
    hyperparameters using ONLY that fold's training portion via
    `core.ml.training.tune_model_family` (the existing Optuna tuner, reused whole --
    not a second, parallel tuning implementation), then evaluate the tuned model on the
    outer fold's untouched test portion. This is what prevents the optimistic bias of
    tuning and evaluating on the same data -- the outer test fold never influences
    which hyperparameters were chosen for it.
    """
    outer_folds = time_series_cv_folds(features, n_folds=n_outer_folds)
    fold_results = []

    for outer_fold in outer_folds:
        assert_no_chronological_leakage(outer_fold)
        X_outer_train = features.loc[outer_fold.train_index]
        y_outer_train = labels.loc[outer_fold.train_index]
        X_outer_test = features.loc[outer_fold.val_index]
        y_outer_test = labels.loc[outer_fold.val_index]

        best_trial, _all_trials = tune_model_family(
            family, X_outer_train, y_outer_train,
            dataset_version=f"nested_cv_outer_fold_{outer_fold.fold_number}",
            feature_version="nested_cv_inner_tuning",
            n_trials=n_inner_trials, n_cv_folds=n_inner_folds,
        )

        from core.ml.training import build_model

        final_model = build_model(family, best_trial.params)
        final_model.fit(X_outer_train, y_outer_train)
        preds = final_model.predict(X_outer_test)

        fold_results.append(
            NestedCVFoldResult(
                outer_fold_number=outer_fold.fold_number,
                best_inner_params=best_trial.params,
                inner_mean_metrics=best_trial.mean_metrics,
                outer_test_accuracy=float(accuracy_score(y_outer_test, preds)),
                outer_test_precision=float(precision_score(y_outer_test, preds, zero_division=0)),
                outer_test_recall=float(recall_score(y_outer_test, preds, zero_division=0)),
            )
        )
        logger.info(
            "Nested CV outer fold %d/%d: test accuracy=%.4f (inner-tuned params=%s)",
            outer_fold.fold_number, n_outer_folds, fold_results[-1].outer_test_accuracy, best_trial.params,
        )

    return NestedCVReport(family=family, fold_results=fold_results)
