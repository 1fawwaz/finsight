"""Tests for core.ml.evaluation: confusion matrix, learning curve, feature importance,
and SHAP artifact generation."""

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier

from core.ml.evaluation import (
    generate_confusion_matrix,
    generate_feature_importance,
    generate_full_evaluation,
    generate_learning_curve,
    generate_shap_summary,
)


@pytest.fixture()
def fitted_model_and_data():
    rng = np.random.default_rng(0)
    n = 200
    X = pd.DataFrame({"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n), "f3": rng.normal(0, 1, n)})
    y = pd.Series((X["f1"] + rng.normal(0, 0.5, n) > 0).astype(int))
    model = RandomForestClassifier(n_estimators=20, max_depth=3, random_state=42)
    model.fit(X, y)
    return model, X, y


def test_generate_confusion_matrix_shape_and_file(fitted_model_and_data, tmp_path):
    model, X, y = fitted_model_and_data
    result = generate_confusion_matrix(model, X, y, tmp_path)
    assert len(result["matrix"]) == 2
    assert len(result["matrix"][0]) == 2
    assert (tmp_path / "confusion_matrix.png").exists()


def test_generate_learning_curve_returns_fold_progression(tmp_path):
    fold_metrics = [{"fold": 1, "roc_auc": 0.50}, {"fold": 2, "roc_auc": 0.52}, {"fold": 3, "roc_auc": 0.51}]
    result = generate_learning_curve(fold_metrics, "roc_auc", tmp_path)
    assert result["folds"] == [1, 2, 3]
    assert result["values"] == [0.50, 0.52, 0.51]
    assert (tmp_path / "learning_curve.png").exists()


def test_generate_feature_importance_ranks_and_persists(fitted_model_and_data, tmp_path):
    model, X, y = fitted_model_and_data
    result = generate_feature_importance(model, list(X.columns), tmp_path)
    assert set(result["importances"].keys()) == {"f1", "f2", "f3"}
    assert (tmp_path / "feature_importance.png").exists()
    # f1 is the only feature with real signal; it should rank highest.
    assert max(result["importances"], key=result["importances"].get) == "f1"


def test_generate_feature_importance_handles_model_without_attribute(tmp_path):
    class _NoImportances:
        pass

    result = generate_feature_importance(_NoImportances(), ["a", "b"], tmp_path)
    assert result["importances"] == {}
    assert result["png_path"] is None


def test_generate_shap_summary_identifies_the_real_signal_feature(fitted_model_and_data, tmp_path):
    model, X, y = fitted_model_and_data
    result = generate_shap_summary(model, X, tmp_path, sample_size=100)
    assert result["sample_size"] == 100
    assert (tmp_path / "shap_summary.png").exists()
    top_feature = list(result["mean_abs_shap"].keys())[0]
    assert top_feature == "f1"  # the feature that actually drives the label


def test_generate_full_evaluation_persists_summary_json(fitted_model_and_data, tmp_path, monkeypatch):
    import core.ml.evaluation as evaluation_module

    monkeypatch.setattr(evaluation_module, "EVALUATION_DIR", tmp_path)
    model, X, y = fitted_model_and_data
    fold_metrics = [{"fold": 1, "roc_auc": 0.50}, {"fold": 2, "roc_auc": 0.53}]
    leakage_audit = {col: {"correlation_with_label": 0.1, "leakage_risk": False} for col in X.columns}

    result = generate_full_evaluation(model, "test_model_v1", X, y, fold_metrics, "roc_auc", leakage_audit)

    assert result["model_version"] == "test_model_v1"
    assert (tmp_path / "test_model_v1" / "evaluation_summary.json").exists()
    assert result["leakage_features_flagged"] == []
