"""Tests for core.ml.baseline: the naive persistence baseline every model must beat."""

import pandas as pd
import pytest

from core.ml.baseline import naive_baseline_metrics, naive_persistence_predictions


def test_naive_persistence_predicts_up_when_last_return_positive():
    features = pd.DataFrame({"lag_return_1": [0.01, -0.02, 0.0, 0.005]})
    predictions = naive_persistence_predictions(features)
    assert predictions.tolist() == [1, 0, 0, 1]


def test_naive_persistence_requires_lag_return_1_column():
    with pytest.raises(ValueError, match="lag_return_1"):
        naive_persistence_predictions(pd.DataFrame({"other": [1, 2, 3]}))


def test_naive_baseline_metrics_perfect_when_predictions_match_labels():
    features = pd.DataFrame({"lag_return_1": [0.01, -0.02, 0.01, -0.01]})
    labels = pd.Series([1, 0, 1, 0])
    metrics = naive_baseline_metrics(features, labels)
    assert metrics["accuracy"] == pytest.approx(1.0)
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1"] == pytest.approx(1.0)


def test_naive_baseline_metrics_returns_all_four_keys():
    features = pd.DataFrame({"lag_return_1": [0.01, -0.02, 0.01, -0.01]})
    labels = pd.Series([1, 1, 0, 0])
    metrics = naive_baseline_metrics(features, labels)
    assert set(metrics.keys()) == {"accuracy", "precision", "recall", "f1"}
    assert all(0.0 <= v <= 1.0 for v in metrics.values())
