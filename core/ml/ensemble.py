"""Phase 3 Step 2.9: a soft-voting ensemble across independently-trained model
families -- averages predict_proba outputs. A single, well-defined architectural
change from a solo model, and picklable (joblib) as long as every constituent is.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class SoftVotingEnsemble:
    """Averages predict_proba across a list of already-fitted binary classifiers."""

    def __init__(self, models: list, feature_names: list[str] | None = None):
        if not models:
            raise ValueError("SoftVotingEnsemble requires at least one model.")
        self.models = models
        if feature_names is not None:
            self.feature_names_in_ = feature_names

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        probas = [m.predict_proba(X) for m in self.models]
        return np.mean(probas, axis=0)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        proba = self.predict_proba(X)
        return (proba[:, 1] >= 0.5).astype(int)

    @property
    def feature_importances_(self) -> np.ndarray:
        importances = [m.feature_importances_ for m in self.models if hasattr(m, "feature_importances_")]
        if len(importances) != len(self.models):
            raise AttributeError("Not every constituent model exposes feature_importances_.")
        return np.mean(importances, axis=0)
