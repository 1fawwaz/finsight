"""Tests for core.ml.ensemble: soft-voting ensemble across model families."""

import numpy as np
import pandas as pd
import pytest

from core.ml.ensemble import SoftVotingEnsemble


class _FixedProbaModel:
    def __init__(self, p_up: float):
        self.p_up = p_up

    def predict_proba(self, X):
        return np.array([[1 - self.p_up, self.p_up]] * len(X))


class _ModelWithImportances(_FixedProbaModel):
    def __init__(self, p_up: float, importances):
        super().__init__(p_up)
        self.feature_importances_ = np.array(importances)


def test_ensemble_averages_predict_proba_across_models():
    ensemble = SoftVotingEnsemble([_FixedProbaModel(0.8), _FixedProbaModel(0.4)])
    X = pd.DataFrame({"f": [1, 2, 3]})
    proba = ensemble.predict_proba(X)
    assert proba.shape == (3, 2)
    assert np.allclose(proba[:, 1], 0.6)  # (0.8 + 0.4) / 2


def test_ensemble_predict_thresholds_at_half():
    ensemble = SoftVotingEnsemble([_FixedProbaModel(0.7), _FixedProbaModel(0.7)])
    X = pd.DataFrame({"f": [1, 2]})
    assert list(ensemble.predict(X)) == [1, 1]

    ensemble_low = SoftVotingEnsemble([_FixedProbaModel(0.2), _FixedProbaModel(0.3)])
    assert list(ensemble_low.predict(X)) == [0, 0]


def test_ensemble_requires_at_least_one_model():
    with pytest.raises(ValueError, match="at least one model"):
        SoftVotingEnsemble([])


def test_ensemble_feature_importances_averages_across_constituents():
    ensemble = SoftVotingEnsemble([_ModelWithImportances(0.5, [0.2, 0.8]), _ModelWithImportances(0.5, [0.6, 0.4])])
    assert np.allclose(ensemble.feature_importances_, [0.4, 0.6])


def test_ensemble_feature_importances_raises_if_any_constituent_lacks_it():
    ensemble = SoftVotingEnsemble([_ModelWithImportances(0.5, [0.5, 0.5]), _FixedProbaModel(0.5)])
    with pytest.raises(AttributeError):
        _ = ensemble.feature_importances_
