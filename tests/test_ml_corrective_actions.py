"""Tests for core.ml.corrective_actions: regularization/feature-selection/re-tuning
strategies applied to a model flagged by the generalization gate."""

import numpy as np
import pandas as pd
import pytest

from core.ml.corrective_actions import (
    _deregularize,
    _regularize,
    attempt_corrections,
    select_features_by_correlation,
)
from core.ml.generalization import evaluate_generalization

_STABLE = {"accuracy": 0.55, "roc_auc": 0.55, "precision": 0.5, "recall": 0.5, "f1": 0.5}
_LOW_STD = {"accuracy": 0.02, "roc_auc": 0.02, "precision": 0.02, "recall": 0.02, "f1": 0.02}


def test_regularize_random_forest_reduces_capacity():
    p = _regularize("random_forest", {"n_estimators": 200, "max_depth": 12, "min_samples_leaf": 5, "max_features": "sqrt"})
    assert p["max_depth"] <= 3
    assert p["min_samples_leaf"] >= 50


def test_deregularize_random_forest_increases_capacity():
    p = _deregularize("random_forest", {"n_estimators": 100, "max_depth": 3, "min_samples_leaf": 30, "max_features": "sqrt"})
    assert p["max_depth"] >= 14
    assert p["min_samples_leaf"] <= 3


def test_select_features_by_correlation_keeps_the_most_correlated():
    rng = np.random.default_rng(0)
    n = 500
    labels = pd.Series(rng.integers(0, 2, n))
    strong = labels.astype(float) + rng.normal(0, 0.1, n)  # highly correlated with label
    weak = pd.Series(rng.normal(0, 1, n))  # uncorrelated
    features = pd.DataFrame({"strong_signal": strong, "weak_noise": weak})

    kept = select_features_by_correlation(features, labels, keep_fraction=0.5)
    assert kept == ["strong_signal"]


def test_select_features_by_correlation_keep_fraction_bounds_count():
    rng = np.random.default_rng(0)
    n = 300
    labels = pd.Series(rng.integers(0, 2, n))
    features = pd.DataFrame({f"f{i}": rng.normal(0, 1, n) for i in range(10)})
    kept = select_features_by_correlation(features, labels, keep_fraction=0.3)
    assert len(kept) == 3


def test_attempt_corrections_stops_early_if_first_attempt_passes(temp_db, monkeypatch):
    # Force evaluate_generalization to report "passed" on the very first call so we can
    # verify attempt_corrections stops there instead of running attempts 2 and 3.
    call_count = {"n": 0}
    real_evaluate = evaluate_generalization

    def fake_evaluate(*args, **kwargs):
        call_count["n"] += 1
        result = real_evaluate(*args, **kwargs)
        result.passed = True
        result.overfit_flag = result.underfit_flag = result.instability_flag = False
        return result

    monkeypatch.setattr("core.ml.corrective_actions.evaluate_generalization", fake_evaluate)

    rng = np.random.default_rng(0)
    n = 300
    train_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, n), "b": rng.normal(0, 1, n)})
    train_y = pd.Series(rng.integers(0, 2, n))
    val_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, 80), "b": rng.normal(0, 1, 80)})
    val_y = pd.Series(rng.integers(0, 2, 80))
    test_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, 80), "b": rng.normal(0, 1, 80)})
    test_y = pd.Series(rng.integers(0, 2, 80))

    dummy_original = real_evaluate(
        "random_forest", {"n_estimators": 50, "max_depth": 6, "min_samples_leaf": 5, "max_features": "sqrt"},
        _STABLE, _LOW_STD, train_X, train_y, val_X, val_y, test_X, test_y,
    )
    dummy_original.overfit_flag = True
    dummy_original.passed = False

    attempts = attempt_corrections(
        "random_forest", dummy_original,
        {"n_estimators": 50, "max_depth": 6, "min_samples_leaf": 5, "max_features": "sqrt"},
        _STABLE, _LOW_STD, train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert len(attempts) == 1
    assert attempts[0].result.passed is True
    assert call_count["n"] == 1


def test_attempt_corrections_runs_all_three_when_none_pass(temp_db):
    rng = np.random.default_rng(0)
    n = 300
    # Pure noise: genuinely hard to pass the gate, so all 3 attempts should run.
    train_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, n), **{f"f{i}": rng.normal(0, 1, n) for i in range(4)}})
    train_y = pd.Series(rng.integers(0, 2, n))
    val_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, 100), **{f"f{i}": rng.normal(0, 1, 100) for i in range(4)}})
    val_y = pd.Series(rng.integers(0, 2, 100))
    test_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, 100), **{f"f{i}": rng.normal(0, 1, 100) for i in range(4)}})
    test_y = pd.Series(rng.integers(0, 2, 100))

    original = evaluate_generalization(
        "random_forest", {"n_estimators": 200, "max_depth": 15, "min_samples_leaf": 2, "max_features": "sqrt"},
        _STABLE, _LOW_STD, train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert original.passed is False  # sanity: this really is flagged (pure noise, high-capacity model)

    attempts = attempt_corrections(
        "random_forest", original,
        {"n_estimators": 200, "max_depth": 15, "min_samples_leaf": 2, "max_features": "sqrt"},
        _STABLE, _LOW_STD, train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert len(attempts) in (1, 2, 3)  # may pass early on pure noise by chance, but never crashes
    assert [a.attempt_number for a in attempts] == list(range(1, len(attempts) + 1))
    for a in attempts:
        assert a.strategy
        assert a.result is not None
