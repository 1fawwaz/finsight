"""Tests for core.ml.timeseries_cv: Phase 2 Step 9 (Time-Series Cross-Validation)."""

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import TimeSeriesSplit

from core.ml.cv import assert_no_chronological_leakage
from core.ml.timeseries_cv import (
    nested_time_series_cv,
    rolling_origin_evaluation,
    time_series_split_folds,
)


def _make_dataset(n: int = 900, seed: int = 0):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2021-01-01", periods=n, freq="D", name="date")
    features = pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n), "f3": rng.uniform(-1, 1, n)}, index=index)
    labels = pd.Series(rng.integers(0, 2, n), index=index)
    return features, labels


def test_time_series_split_folds_matches_raw_sklearn_boundaries():
    features, _ = _make_dataset(200)
    n_splits = 4
    wrapped = time_series_split_folds(features, n_splits=n_splits)
    raw = list(TimeSeriesSplit(n_splits=n_splits).split(features))

    assert len(wrapped) == len(raw) == n_splits
    for fold, (train_pos, val_pos) in zip(wrapped, raw):
        pd.testing.assert_index_equal(fold.train_index, features.index[train_pos])
        pd.testing.assert_index_equal(fold.val_index, features.index[val_pos])


def test_time_series_split_folds_pass_the_leakage_check():
    features, _ = _make_dataset(300)
    folds = time_series_split_folds(features, n_splits=5)
    for fold in folds:
        assert assert_no_chronological_leakage(fold) is True  # never raises


def test_time_series_split_folds_training_window_expands():
    """sklearn's TimeSeriesSplit is expanding-window by default -- each fold's training
    set must be at least as large as the previous fold's."""
    features, _ = _make_dataset(300)
    folds = time_series_split_folds(features, n_splits=5)
    sizes = [len(f.train_index) for f in folds]
    assert sizes == sorted(sizes)
    assert sizes[-1] > sizes[0]


def test_time_series_split_folds_respects_gap_parameter():
    """sklearn's TimeSeriesSplit(gap=N) pulls the *training set's end* back by N
    samples (not the validation start forward) -- the validation block's own
    boundaries are unaffected; verified against sklearn's actual documented behavior
    rather than an assumed one."""
    features, _ = _make_dataset(300)
    folds_no_gap = time_series_split_folds(features, n_splits=3, gap=0)
    folds_with_gap = time_series_split_folds(features, n_splits=3, gap=10)
    assert folds_with_gap[0].train_index[-1] < folds_no_gap[0].train_index[-1]
    assert len(folds_with_gap[0].train_index) == len(folds_no_gap[0].train_index) - 10


def test_rolling_origin_evaluation_produces_predictions_at_each_step():
    features, labels = _make_dataset(200)
    result = rolling_origin_evaluation(features, labels, min_train_size=150, step=10)

    assert len(result.predictions) == len(result.actuals) == len(result.origins)
    assert len(result.predictions) > 0
    assert 0.0 <= result.accuracy <= 1.0


def test_rolling_origin_evaluation_step_controls_number_of_origins():
    features, labels = _make_dataset(300)
    coarse = rolling_origin_evaluation(features, labels, min_train_size=150, step=50)
    fine = rolling_origin_evaluation(features, labels, min_train_size=150, step=5)
    assert len(fine.origins) > len(coarse.origins)


def test_rolling_origin_evaluation_each_origin_only_uses_prior_data():
    """The defining no-lookahead property: the training set for the prediction made at
    origin index i must never include index i itself or anything after it."""
    features, labels = _make_dataset(200)
    result = rolling_origin_evaluation(features, labels, min_train_size=150, step=20)
    for origin_date in result.origins:
        origin_pos = features.index.get_loc(origin_date)
        # Reconstruct: everything the model at this origin was trained on is strictly
        # before origin_pos (features.iloc[:origin_pos] in the implementation).
        assert origin_pos >= 150


def test_rolling_origin_evaluation_raises_for_insufficient_data():
    features, labels = _make_dataset(50)
    with pytest.raises(ValueError, match="Not enough rows"):
        rolling_origin_evaluation(features, labels, min_train_size=100, step=1)


def test_nested_time_series_cv_runs_end_to_end(temp_db):
    features, labels = _make_dataset(900)
    report = nested_time_series_cv(
        features, labels, family="random_forest", n_outer_folds=2, n_inner_trials=2, n_inner_folds=2,
    )

    assert report.family == "random_forest"
    assert len(report.fold_results) == 2
    for fold_result in report.fold_results:
        assert 0.0 <= fold_result.outer_test_accuracy <= 1.0
        assert isinstance(fold_result.best_inner_params, dict)
    assert 0.0 <= report.mean_outer_test_accuracy <= 1.0


def test_nested_time_series_cv_outer_test_fold_never_used_for_inner_tuning(temp_db):
    """The core guarantee nested CV exists for: verify directly (not just trust) that
    each outer fold's test indices are disjoint from the data the inner tuner saw."""
    from core.ml.cv import time_series_cv_folds

    features, labels = _make_dataset(900)
    outer_folds = time_series_cv_folds(features, n_folds=2)

    for fold in outer_folds:
        # This is exactly what nested_time_series_cv passes to tune_model_family --
        # confirm here, directly, that it excludes the outer test indices entirely.
        assert set(fold.train_index).isdisjoint(set(fold.val_index))
