"""Naive baseline for the Phase 3 direction-classification target: every trained model
must be reported against this before any tuning claim means anything.

Target: predict whether the next trading session's close will be higher than the
current session's close (binary). The natural naive baseline for that target is
persistence -- "tomorrow repeats today's direction" -- since there's no meaningful
"moving average" baseline for a binary up/down label the way there is for a continuous
price target.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def naive_persistence_predictions(features: pd.DataFrame) -> pd.Series:
    """Predicts 1 (up) if the most recent daily return (the `lag_return_1` feature,
    already computed and leak-free) was positive, else 0. Requires no training and no
    hyperparameters -- this is the floor every real model must clear."""
    if "lag_return_1" not in features.columns:
        raise ValueError("naive_persistence_predictions requires a 'lag_return_1' column.")
    return (features["lag_return_1"] > 0).astype(int)


def naive_baseline_metrics(features: pd.DataFrame, labels: pd.Series) -> dict:
    """Accuracy/precision/recall/F1 of the naive persistence baseline on `labels`."""
    from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

    predictions = naive_persistence_predictions(features)
    return {
        "accuracy": float(accuracy_score(labels, predictions)),
        "precision": float(precision_score(labels, predictions, zero_division=0)),
        "recall": float(recall_score(labels, predictions, zero_division=0)),
        "f1": float(f1_score(labels, predictions, zero_division=0)),
    }
