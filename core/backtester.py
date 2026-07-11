"""Walk-forward backtest for the direction classifier: strictly chronological train/test splits,
so a fold's model never sees data from its own or any later test window.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, confusion_matrix, precision_score, recall_score

from core.ml_model import train_model


@dataclass
class BacktestResult:
    accuracy: float
    precision: float
    recall: float
    confusion: np.ndarray  # 2x2, rows/cols ordered [0, 1]
    predictions: pd.DataFrame  # index=date, columns=[predicted, actual, probability]
    equity_signal: pd.Series  # cumulative growth of $1 following the model's long/flat signal
    equity_buy_hold: pd.Series  # cumulative growth of $1 always holding


def walk_forward_backtest(
    features: pd.DataFrame,
    labels: pd.Series,
    close: pd.Series,
    train_window: int = 252,
    test_window: int = 21,
) -> BacktestResult:
    """Roll a fixed training window forward in non-overlapping test blocks.

    For each fold: train strictly on the `train_window` rows immediately preceding the
    fold, predict on the next `test_window` rows, then advance past that block. No shuffling,
    no fold ever trains on data from its own or a future test window.
    """
    n = len(features)
    all_dates: list = []
    all_preds: list[int] = []
    all_true: list[int] = []
    all_proba: list[float] = []

    i = train_window
    while i + test_window <= n:
        X_train = features.iloc[i - train_window : i]
        y_train = labels.iloc[i - train_window : i]
        X_test = features.iloc[i : i + test_window]
        y_test = labels.iloc[i : i + test_window]

        model = train_model(X_train, y_train)
        proba = model.predict_proba(X_test)[:, 1]
        preds = (proba >= 0.5).astype(int)

        all_dates.extend(X_test.index.tolist())
        all_preds.extend(preds.tolist())
        all_true.extend(y_test.astype(int).tolist())
        all_proba.extend(proba.tolist())

        i += test_window

    if not all_dates:
        raise ValueError("Not enough history for even one walk-forward fold; need more price data.")

    predictions = pd.DataFrame(
        {"predicted": all_preds, "actual": all_true, "probability": all_proba}, index=pd.Index(all_dates, name="date")
    )

    accuracy = accuracy_score(all_true, all_preds)
    precision = precision_score(all_true, all_preds, zero_division=0)
    recall = recall_score(all_true, all_preds, zero_division=0)
    confusion = confusion_matrix(all_true, all_preds, labels=[0, 1])

    next_day_return = (close.shift(-1) / close - 1).reindex(predictions.index)
    strategy_returns = next_day_return * predictions["predicted"]
    equity_signal = (1 + strategy_returns.fillna(0)).cumprod()
    equity_buy_hold = (1 + next_day_return.fillna(0)).cumprod()

    return BacktestResult(
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        confusion=confusion,
        predictions=predictions,
        equity_signal=equity_signal,
        equity_buy_hold=equity_buy_hold,
    )
