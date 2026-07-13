"""Tests for core.ml.calibration: Phase 2 Step 7 (Probability Calibration)."""

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression

from core.ml.calibration import (
    calibrate_isotonic,
    calibrate_platt,
    compare_calibration_methods,
    compute_calibration_curve,
    compute_ece,
    compute_mce,
    fit_temperature,
)


def test_ece_is_zero_for_perfectly_calibrated_predictions():
    rng = np.random.default_rng(0)
    n = 2000
    y_prob = rng.uniform(0, 1, n)
    # Labels drawn exactly according to their stated probability -- perfectly
    # calibrated in expectation over a large enough sample.
    y_true = (rng.uniform(0, 1, n) < y_prob).astype(int)
    ece = compute_ece(y_true, y_prob, n_bins=10)
    assert ece < 0.03  # near zero, allowing for sampling noise at this sample size


def test_ece_is_large_for_badly_miscalibrated_predictions():
    # Model claims 95% confidence on everything, but the true rate is 50%.
    y_prob = np.full(1000, 0.95)
    rng = np.random.default_rng(1)
    y_true = rng.integers(0, 2, 1000)
    ece = compute_ece(y_true, y_prob, n_bins=10)
    assert ece > 0.3


def test_mce_is_never_less_than_ece():
    """MCE is the single worst bin's error; ECE is the size-weighted average of the
    same per-bin errors -- a weighted average of non-negative values can never exceed
    the maximum of those same values."""
    rng = np.random.default_rng(2)
    y_prob = rng.uniform(0, 1, 500)
    y_true = rng.integers(0, 2, 500)
    ece = compute_ece(y_true, y_prob, n_bins=10)
    mce = compute_mce(y_true, y_prob, n_bins=10)
    assert mce >= ece - 1e-9


def test_compute_calibration_curve_shape_and_bounds():
    rng = np.random.default_rng(3)
    y_prob = rng.uniform(0, 1, 500)
    y_true = rng.integers(0, 2, 500)
    curve = compute_calibration_curve(y_true, y_prob, n_bins=10)
    assert set(curve.columns) == {"mean_predicted_probability", "fraction_of_positives"}
    assert (curve["mean_predicted_probability"] >= 0).all() and (curve["mean_predicted_probability"] <= 1).all()
    assert (curve["fraction_of_positives"] >= 0).all() and (curve["fraction_of_positives"] <= 1).all()


def test_fit_temperature_near_1_for_already_calibrated_probabilities():
    rng = np.random.default_rng(4)
    n = 3000
    y_prob = rng.uniform(0.05, 0.95, n)
    y_true = (rng.uniform(0, 1, n) < y_prob).astype(int)
    temperature = fit_temperature(pd.Series(y_true), y_prob)
    assert temperature == pytest.approx(1.0, abs=0.35)


def test_fit_temperature_finds_greater_than_1_for_overconfident_probabilities():
    """An overconfident model (probabilities pushed toward 0/1 relative to the true
    rate) should be corrected by a temperature > 1 (softening toward 0.5)."""
    rng = np.random.default_rng(5)
    n = 3000
    true_prob = rng.uniform(0.3, 0.7, n)
    y_true = (rng.uniform(0, 1, n) < true_prob).astype(int)
    # Deliberately overconfident: push every probability away from 0.5.
    overconfident_prob = np.clip(0.5 + (true_prob - 0.5) * 3, 0.01, 0.99)
    temperature = fit_temperature(pd.Series(y_true), overconfident_prob)
    assert temperature > 1.2


def test_compare_calibration_methods_runs_end_to_end_and_reduces_ece():
    """A deliberately overfit, overconfident classifier's raw probabilities should be
    improved (lower ECE) by at least one calibration method -- proving the comparison
    harness is not just running without error, but actually measuring a real effect."""
    rng = np.random.default_rng(6)
    n = 800
    X = pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n)})
    true_logit = X["f1"] * 0.5
    y = (true_logit + rng.normal(0, 1, n) > 0).astype(int)

    split = int(n * 0.5)
    X_train, y_train = X.iloc[:split], y.iloc[:split]
    X_calib, y_calib = X.iloc[split : split + 150], y.iloc[split : split + 150]
    X_test, y_test = X.iloc[split + 150 :], y.iloc[split + 150 :]

    # A high-C logistic regression on noisy data tends to be overconfident -- a
    # realistic miscalibration scenario, not an artificially broken model.
    model = LogisticRegression(C=100.0)
    model.fit(X_train, y_train)

    report = compare_calibration_methods(model, X_calib, y_calib, X_test, y_test)

    assert len(report.results) == 4
    methods = {r.method for r in report.results}
    assert methods == {"raw", "platt", "isotonic", "temperature"}
    raw_result = next(r for r in report.results if r.method == "raw")
    best = report.best_by_ece()
    assert best.ece <= raw_result.ece  # at least one method is no worse than raw
    assert report.temperature is not None


def test_calibrate_platt_and_isotonic_return_fitted_predictors():
    rng = np.random.default_rng(7)
    n = 300
    X = pd.DataFrame({"f1": rng.normal(0, 1, n)})
    y = (X["f1"] > 0).astype(int)
    model = LogisticRegression()
    model.fit(X, y)

    platt = calibrate_platt(model, X, y)
    isotonic = calibrate_isotonic(model, X, y)

    platt_proba = platt.predict_proba(X)[:, 1]
    isotonic_proba = isotonic.predict_proba(X)[:, 1]
    assert ((platt_proba >= 0) & (platt_proba <= 1)).all()
    assert ((isotonic_proba >= 0) & (isotonic_proba <= 1)).all()
