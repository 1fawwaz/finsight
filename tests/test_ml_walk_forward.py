"""Tests for core.ml.walk_forward: Phase 2 Step 8 (Walk-Forward Validation).

Both fold styles reuse existing code (core.ml.cv.time_series_cv_folds for expanding-
window, core.backtester.walk_forward_backtest for rolling-window) -- these tests focus
on the new pieces: rolling_window_folds' boundary generation and the explicit,
non-assertion leakage-verification report.
"""

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from core.ml.cv import CVFold, time_series_cv_folds
from core.ml.walk_forward import (
    rolling_window_folds,
    run_expanding_window_validation,
    run_rolling_window_validation,
    verify_no_leakage_report,
)


def _make_dataset(n: int = 600, seed: int = 0):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2022-01-01", periods=n, freq="D", name="date")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=index).clip(lower=1)
    features = pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n)}, index=index)
    labels = pd.Series((close.shift(-1) > close).astype(float), index=index).iloc[:-1]
    features, close = features.iloc[:-1], close.iloc[:-1]
    return features, labels.astype(int), close


def test_rolling_window_folds_has_fixed_train_size_not_expanding():
    """The defining property that distinguishes rolling from expanding: every fold's
    training window is exactly `train_window` rows, never growing."""
    features, _, _ = _make_dataset(400)
    folds = rolling_window_folds(features, train_window=100, test_window=20)
    assert len(folds) > 1
    train_sizes = {len(f.train_index) for f in folds}
    assert train_sizes == {100}  # every fold, same fixed size


def test_rolling_window_folds_count_matches_manual_calculation():
    features, _, _ = _make_dataset(400)
    train_window, test_window = 100, 20
    folds = rolling_window_folds(features, train_window, test_window)
    n = len(features)
    expected_count = 0
    i = train_window
    while i + test_window <= n:
        expected_count += 1
        i += test_window
    assert len(folds) == expected_count


def test_rolling_window_folds_windows_slide_forward_without_gaps():
    features, _, _ = _make_dataset(400)
    folds = rolling_window_folds(features, train_window=100, test_window=20)
    for a, b in zip(folds, folds[1:]):
        assert b.train_index[0] > a.train_index[0]  # each fold's window starts later than the previous


def test_verify_no_leakage_report_all_pass_for_real_expanding_folds():
    """Real folds from the existing, already-tested time_series_cv_folds must always
    pass -- proving the two systems compose without friction."""
    features, _, _ = _make_dataset(500)
    folds = time_series_cv_folds(features, n_folds=5)
    report = verify_no_leakage_report(folds, style="expanding")
    assert len(report) == len(folds)
    assert report["passed"].all()
    assert set(report["style"]) == {"expanding"}


def test_verify_no_leakage_report_all_pass_for_real_rolling_folds():
    features, _, _ = _make_dataset(400)
    folds = rolling_window_folds(features, train_window=100, test_window=20)
    report = verify_no_leakage_report(folds, style="rolling")
    assert report["passed"].all()


def test_verify_no_leakage_report_catches_a_constructed_leaky_fold():
    """A deliberately corrupted fold (val range starts before train range ends) must be
    caught and recorded as a failure in the report -- not silently passed, and not an
    uncaught exception that would abort the whole report."""
    features, _, _ = _make_dataset(200)
    good_folds = time_series_cv_folds(features, n_folds=3)
    leaky_fold = replace(good_folds[0], val_date_range=(good_folds[0].train_date_range[0], good_folds[0].train_date_range[1]))

    report = verify_no_leakage_report([leaky_fold], style="expanding")

    assert len(report) == 1
    assert report.iloc[0]["passed"] == False  # noqa: E712 -- explicit bool comparison for clarity in a pandas row
    assert report.iloc[0]["failure_reason"] is not None


def test_run_rolling_window_validation_across_multiple_configs():
    features, labels, close = _make_dataset(600)
    results = run_rolling_window_validation(features, labels, close, window_configs=[(100, 20), (200, 30)])

    assert len(results) == 2
    for r in results:
        assert r.style == "rolling"
        assert r.n_folds > 0
        assert r.leakage_report["passed"].all()
        assert 0.0 <= r.mean_accuracy <= 1.0


def test_run_expanding_window_validation_across_multiple_configs():
    features, labels, close = _make_dataset(600)
    results = run_expanding_window_validation(features, labels, n_folds_list=[3, 5])

    assert len(results) == 2
    for r in results:
        assert r.style == "expanding"
        assert r.n_folds > 0
        assert r.leakage_report["passed"].all()
        assert 0.0 <= r.mean_accuracy <= 1.0


def test_run_expanding_window_validation_handles_infeasible_fold_count_gracefully():
    """Too many folds for the available data must not crash the whole comparison --
    that configuration reports NaN/0-folds rather than raising past the caller."""
    features, labels, close = _make_dataset(60)  # deliberately small
    results = run_expanding_window_validation(features, labels, n_folds_list=[3, 500])

    assert len(results) == 2
    infeasible = next(r for r in results if r.config["n_folds"] == 500)
    assert infeasible.n_folds == 0
    assert pd.isna(infeasible.mean_accuracy)
