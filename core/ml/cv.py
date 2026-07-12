"""Chronological data splitting for the Phase 3 ML pipeline: a fixed train/validation/
test split by unique date (never by row count, so a heavily-traded symbol with more
rows can't skew the cutoff), plus expanding-window time-series cross-validation within
the train region -- with an explicit, evidence-producing assertion that no
validation-fold timestamp ever precedes a training-fold timestamp in the same split.

Operates on a (symbol, date)-MultiIndexed features DataFrame: splitting by unique date
(not row index) applies the same temporal boundary to every symbol in the panel
simultaneously, which is what prevents one symbol's "future" from leaking into
another's "past" fold.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

RANDOM_STATE = 42


@dataclass
class ChronologicalSplit:
    train_dates: tuple
    val_dates: tuple
    test_dates: tuple
    train_index: pd.Index
    val_index: pd.Index
    test_index: pd.Index


def chronological_train_val_test_split(
    features: pd.DataFrame, train_frac: float = 0.70, val_frac: float = 0.15
) -> ChronologicalSplit:
    """Split by unique date: test is always the most recent slice, untouched until
    final evaluation. `train_frac + val_frac` must be < 1.0 so a real test slice remains."""
    if not 0 < train_frac < 1 or not 0 < val_frac < 1 or train_frac + val_frac >= 1:
        raise ValueError("train_frac and val_frac must each be in (0, 1) and sum to < 1.")

    dates = np.sort(features.index.get_level_values("date").unique())
    n = len(dates)
    train_end_idx = int(n * train_frac)
    val_end_idx = int(n * (train_frac + val_frac))
    if train_end_idx < 1 or val_end_idx <= train_end_idx or val_end_idx >= n:
        raise ValueError(f"Not enough unique dates ({n}) to split at train_frac={train_frac}, val_frac={val_frac}.")

    train_dates = dates[:train_end_idx]
    val_dates = dates[train_end_idx:val_end_idx]
    test_dates = dates[val_end_idx:]

    date_level = features.index.get_level_values("date")
    train_index = features.index[date_level.isin(train_dates)]
    val_index = features.index[date_level.isin(val_dates)]
    test_index = features.index[date_level.isin(test_dates)]

    return ChronologicalSplit(
        train_dates=(train_dates.min(), train_dates.max()),
        val_dates=(val_dates.min(), val_dates.max()),
        test_dates=(test_dates.min(), test_dates.max()),
        train_index=train_index,
        val_index=val_index,
        test_index=test_index,
    )


@dataclass
class CVFold:
    fold_number: int
    train_index: pd.Index
    val_index: pd.Index
    train_date_range: tuple
    val_date_range: tuple


def time_series_cv_folds(features: pd.DataFrame, n_folds: int = 5) -> list[CVFold]:
    """Expanding-window chronological CV folds over unique dates within `features`
    (callers pass the train+val region only -- test must never appear here). Fold i's
    training window grows to include everything before fold i's validation window, a
    contiguous later date range -- classic walk-forward validation, applied uniformly
    across every symbol in the panel via the shared date index.
    """
    dates = np.sort(features.index.get_level_values("date").unique())
    n = len(dates)
    fold_size = n // (n_folds + 1)
    if fold_size < 1:
        raise ValueError(f"Not enough unique dates ({n}) for {n_folds} folds.")

    date_level = features.index.get_level_values("date")
    folds: list[CVFold] = []
    for i in range(n_folds):
        train_end = fold_size * (i + 1)
        val_end = fold_size * (i + 2) if i < n_folds - 1 else n
        train_dates = dates[:train_end]
        val_dates = dates[train_end:val_end]
        if len(val_dates) == 0:
            continue
        train_index = features.index[date_level.isin(train_dates)]
        val_index = features.index[date_level.isin(val_dates)]
        folds.append(
            CVFold(
                fold_number=i + 1,
                train_index=train_index,
                val_index=val_index,
                train_date_range=(train_dates.min(), train_dates.max()),
                val_date_range=(val_dates.min(), val_dates.max()),
            )
        )
    return folds


def assert_no_chronological_leakage(fold: CVFold) -> bool:
    """The mandatory chronological-integrity assertion: no validation-fold timestamp
    may precede or equal any training-fold timestamp in the same split. Raises
    AssertionError with the actual dates as evidence on failure -- never a silent pass."""
    train_max = fold.train_date_range[1]
    val_min = fold.val_date_range[0]
    assert val_min > train_max, (
        f"Fold {fold.fold_number}: chronological leakage -- validation min date "
        f"{val_min} does not strictly follow training max date {train_max}"
    )
    return True
