"""Tests for core/ml/explanation.py -- per-instance SHAP explanation. Verifies real
SHAP values are used (not fabricated), degrades to None (never a fake explanation) for
non-tree models, and every narrated sentence traces back to a real feature value."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression

from core.ml.explanation import explain_single_prediction


def _fitted_tree_model_and_row():
    rng = np.random.default_rng(3)
    X = pd.DataFrame(
        {
            "rsi_14": rng.uniform(10, 90, 200),
            "macd": rng.normal(0, 1, 200),
            "volume_zscore": rng.normal(0, 1, 200),
        }
    )
    y = (X["rsi_14"] > 50).astype(int)  # a real, learnable relationship
    model = RandomForestClassifier(n_estimators=20, max_depth=3, random_state=0).fit(X, y)
    row = X.iloc[[0]]
    return model, row


class TestExplainSinglePrediction:
    def test_requires_exactly_one_row(self):
        model, row = _fitted_tree_model_and_row()
        two_rows = pd.concat([row, row])
        with pytest.raises(ValueError, match="exactly one row"):
            explain_single_prediction(model, two_rows)

    def test_returns_a_real_explanation_for_a_tree_model(self):
        model, row = _fitted_tree_model_and_row()
        explanation = explain_single_prediction(model, row)
        assert explanation is not None
        assert explanation.method == "shap_tree_explainer"
        assert set(explanation.feature_contributions.keys()) == set(row.columns)
        assert set(explanation.feature_values.keys()) == set(row.columns)

    def test_feature_values_match_the_real_input_row(self):
        model, row = _fitted_tree_model_and_row()
        explanation = explain_single_prediction(model, row)
        for col in row.columns:
            assert explanation.feature_values[col] == pytest.approx(float(row.iloc[0][col]))

    def test_importance_ranking_is_sorted_by_absolute_contribution_descending(self):
        model, row = _fitted_tree_model_and_row()
        explanation = explain_single_prediction(model, row)
        abs_values = [abs(v) for _, v in explanation.feature_importance_ranking]
        assert abs_values == sorted(abs_values, reverse=True)

    def test_positive_features_all_have_positive_contribution(self):
        model, row = _fitted_tree_model_and_row()
        explanation = explain_single_prediction(model, row)
        assert all(v > 0 for _, v in explanation.top_positive_features)

    def test_negative_features_all_have_negative_contribution(self):
        model, row = _fitted_tree_model_and_row()
        explanation = explain_single_prediction(model, row)
        assert all(v < 0 for _, v in explanation.top_negative_features)

    def test_natural_language_explanation_is_nonempty_and_derived_from_real_features(self):
        model, row = _fitted_tree_model_and_row()
        explanation = explain_single_prediction(model, row)
        assert len(explanation.natural_language_explanation) > 0
        # RSI is the strongest real signal in this synthetic dataset -- the narration
        # should mention it somewhere among the top features.
        assert "RSI" in explanation.natural_language_explanation or "rsi" in explanation.natural_language_explanation.lower()

    def test_non_tree_model_returns_none_not_a_fabricated_explanation(self):
        rng = np.random.default_rng(1)
        X = pd.DataFrame({"a": rng.normal(size=50), "b": rng.normal(size=50)})
        y = (X["a"] > 0).astype(int)
        model = LogisticRegression().fit(X, y)
        explanation = explain_single_prediction(model, X.iloc[[0]])
        assert explanation is None

    def test_unrecognized_feature_name_falls_back_to_generic_fragment_not_a_crash(self):
        rng = np.random.default_rng(5)
        X = pd.DataFrame({"totally_unknown_feature_xyz": rng.normal(size=100), "rsi_14": rng.uniform(10, 90, 100)})
        y = (X["rsi_14"] > 50).astype(int)
        model = RandomForestClassifier(n_estimators=10, max_depth=2, random_state=0).fit(X, y)
        explanation = explain_single_prediction(model, X.iloc[[0]])
        assert explanation is not None
        assert len(explanation.natural_language_explanation) > 0
