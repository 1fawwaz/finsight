"""Tests for core.ml.generalization: the mandatory overfit/underfit/fold-instability gate."""

import numpy as np
import pandas as pd
import pytest

from core.ml.generalization import (
    FOLD_INSTABILITY_THRESHOLD_PCT,
    OVERFIT_GAP_THRESHOLD_PCT,
    UNDERFIT_BASELINE_MARGIN_PCT,
    audit_feature_leakage,
    evaluate_generalization,
)


def _split(n: int, seed: int, signal_strength: float) -> tuple[pd.DataFrame, pd.Series]:
    """A feature that's genuinely predictive of the label, strength tunable."""
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    label_prob = 1 / (1 + np.exp(-signal_strength * signal))
    labels = (rng.uniform(0, 1, n) < label_prob).astype(int)
    features = pd.DataFrame({"lag_return_1": signal * 0.01, "noise": noise})
    return features, pd.Series(labels)


_STABLE_FOLD_METRICS = {"accuracy": 0.55, "roc_auc": 0.55, "precision": 0.5, "recall": 0.5, "f1": 0.5}
_LOW_STD_FOLD_METRICS = {"accuracy": 0.02, "roc_auc": 0.02, "precision": 0.02, "recall": 0.02, "f1": 0.02}
_HIGH_STD_FOLD_METRICS = {"accuracy": 0.20, "roc_auc": 0.20, "precision": 0.20, "recall": 0.20, "f1": 0.20}


def test_evaluate_generalization_flags_instability_from_high_fold_variance(temp_db):
    train_X, train_y = _split(400, seed=1, signal_strength=2.0)
    val_X, val_y = _split(100, seed=2, signal_strength=2.0)
    test_X, test_y = _split(100, seed=3, signal_strength=2.0)

    result = evaluate_generalization(
        "random_forest",
        {"n_estimators": 50, "max_depth": 4, "min_samples_leaf": 5, "max_features": "sqrt"},
        _STABLE_FOLD_METRICS,
        _HIGH_STD_FOLD_METRICS,
        train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert result.instability_flag is True
    assert result.fold_std_pct > FOLD_INSTABILITY_THRESHOLD_PCT


def test_evaluate_generalization_does_not_flag_instability_from_low_fold_variance(temp_db):
    train_X, train_y = _split(400, seed=1, signal_strength=2.0)
    val_X, val_y = _split(100, seed=2, signal_strength=2.0)
    test_X, test_y = _split(100, seed=3, signal_strength=2.0)

    result = evaluate_generalization(
        "random_forest",
        {"n_estimators": 50, "max_depth": 4, "min_samples_leaf": 5, "max_features": "sqrt"},
        _STABLE_FOLD_METRICS,
        _LOW_STD_FOLD_METRICS,
        train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert result.instability_flag is False
    assert result.fold_std_pct < FOLD_INSTABILITY_THRESHOLD_PCT


def test_evaluate_generalization_flags_underfit_when_no_signal(temp_db):
    # Pure noise -- no real relationship between features and label. A well-regularized
    # model should land near baseline on both train and val, tripping the underfit flag.
    # A large sample size keeps both the model's and the naive baseline's accuracy close
    # to their true ~50% expectation, instead of a small sample's own random deviation
    # (a low-baseline denominator otherwise inflates tiny absolute noise into large-
    # looking relative percentages).
    rng = np.random.default_rng(0)
    n = 3000
    train_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, n), "noise": rng.normal(0, 1, n)})
    train_y = pd.Series(rng.integers(0, 2, n))
    val_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, 800), "noise": rng.normal(0, 1, 800)})
    val_y = pd.Series(rng.integers(0, 2, 800))
    test_X = pd.DataFrame({"lag_return_1": rng.normal(0, 0.01, 800), "noise": rng.normal(0, 1, 800)})
    test_y = pd.Series(rng.integers(0, 2, 800))

    result = evaluate_generalization(
        "random_forest",
        # A single depth-1 stump has essentially no capacity to memorize noise, so it
        # can't overfit -- this isolates the underfit signal instead of also tripping
        # the overfit flag the way a deeper, higher-capacity model would on pure noise.
        {"n_estimators": 1, "max_depth": 1, "min_samples_leaf": 50, "max_features": "sqrt"},
        _STABLE_FOLD_METRICS,
        _LOW_STD_FOLD_METRICS,
        train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert result.underfit_flag is True
    assert result.overfit_flag is False


def test_evaluate_generalization_returns_full_metric_sets_for_all_three_splits(temp_db):
    train_X, train_y = _split(300, seed=1, signal_strength=1.5)
    val_X, val_y = _split(80, seed=2, signal_strength=1.5)
    test_X, test_y = _split(80, seed=3, signal_strength=1.5)

    result = evaluate_generalization(
        "random_forest",
        {"n_estimators": 30, "max_depth": 3, "min_samples_leaf": 5, "max_features": "sqrt"},
        _STABLE_FOLD_METRICS,
        _LOW_STD_FOLD_METRICS,
        train_X, train_y, val_X, val_y, test_X, test_y,
    )
    for metrics in (result.train_metrics, result.val_metrics, result.test_metrics):
        assert set(metrics.keys()) == {"accuracy", "precision", "recall", "f1", "roc_auc"}
    assert "reasoning" not in result.train_metrics  # sanity: didn't leak dataclass fields into metrics


def test_evaluate_generalization_reasoning_cites_threshold_source(temp_db):
    train_X, train_y = _split(300, seed=1, signal_strength=1.5)
    val_X, val_y = _split(80, seed=2, signal_strength=1.5)
    test_X, test_y = _split(80, seed=3, signal_strength=1.5)

    result = evaluate_generalization(
        "random_forest",
        {"n_estimators": 30, "max_depth": 3, "min_samples_leaf": 5, "max_features": "sqrt"},
        _STABLE_FOLD_METRICS,
        _LOW_STD_FOLD_METRICS,
        train_X, train_y, val_X, val_y, test_X, test_y,
    )
    assert "threshold" in result.reasoning.lower()
    assert "source" in result.reasoning.lower()


def test_audit_feature_leakage_flags_a_feature_identical_to_the_label():
    labels = pd.Series([1, 0, 1, 0, 1, 0, 1, 0, 1, 0])
    features = pd.DataFrame(
        {
            "perfectly_leaky": labels.astype(float),
            "clean_random": [0.1, 0.9, 0.2, 0.8, 0.15, 0.85, 0.3, 0.7, 0.25, 0.75],
        }
    )
    report = audit_feature_leakage(features, labels, threshold=0.95)
    assert report["perfectly_leaky"]["leakage_risk"] is True
    assert report["perfectly_leaky"]["correlation_with_label"] == pytest.approx(1.0)


def test_audit_feature_leakage_does_not_flag_uncorrelated_features():
    rng = np.random.default_rng(0)
    labels = pd.Series(rng.integers(0, 2, 500))
    features = pd.DataFrame({"noise": rng.normal(0, 1, 500)})
    report = audit_feature_leakage(features, labels, threshold=0.95)
    assert report["noise"]["leakage_risk"] is False


def test_audit_feature_leakage_covers_every_feature_not_just_flagged_ones():
    labels = pd.Series([1, 0, 1, 0, 1, 0])
    features = pd.DataFrame({"a": [1, 2, 3, 4, 5, 6], "b": [6, 5, 4, 3, 2, 1], "c": [1, 1, 1, 1, 1, 1]})
    report = audit_feature_leakage(features, labels)
    assert set(report.keys()) == {"a", "b", "c"}
