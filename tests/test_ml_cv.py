"""Tests for core.ml.cv: chronological train/val/test split and walk-forward CV folds."""

import numpy as np
import pandas as pd
import pytest

from core.ml.cv import (
    assert_no_chronological_leakage,
    chronological_train_val_test_split,
    time_series_cv_folds,
)


def _make_panel(n_dates: int = 300, symbols: tuple[str, ...] = ("AAA.NS", "BBB.NS")) -> pd.DataFrame:
    dates = pd.bdate_range("2023-01-01", periods=n_dates).date
    index = pd.MultiIndex.from_product([symbols, dates], names=["symbol", "date"])
    rng = np.random.default_rng(0)
    return pd.DataFrame({"feature_a": rng.normal(size=len(index))}, index=index)


def test_chronological_split_test_is_the_most_recent_slice():
    panel = _make_panel(300)
    split = chronological_train_val_test_split(panel, train_frac=0.7, val_frac=0.15)
    assert split.train_dates[1] < split.val_dates[0]
    assert split.val_dates[1] < split.test_dates[0]


def test_chronological_split_covers_every_row_exactly_once():
    panel = _make_panel(300)
    split = chronological_train_val_test_split(panel)
    total = len(split.train_index) + len(split.val_index) + len(split.test_index)
    assert total == len(panel)
    assert set(split.train_index) | set(split.val_index) | set(split.test_index) == set(panel.index)
    assert not (set(split.train_index) & set(split.val_index))
    assert not (set(split.val_index) & set(split.test_index))


def test_chronological_split_applies_same_boundary_to_every_symbol():
    # The whole point of a panel-aware split: symbol B's rows on a given date must land
    # in the exact same split as symbol A's rows on that date.
    panel = _make_panel(300, symbols=("AAA.NS", "BBB.NS"))
    split = chronological_train_val_test_split(panel)
    train_dates_a = {d for s, d in split.train_index if s == "AAA.NS"}
    train_dates_b = {d for s, d in split.train_index if s == "BBB.NS"}
    assert train_dates_a == train_dates_b


def test_chronological_split_rejects_invalid_fractions():
    panel = _make_panel(300)
    with pytest.raises(ValueError):
        chronological_train_val_test_split(panel, train_frac=0.8, val_frac=0.3)


def test_time_series_cv_folds_are_expanding_and_walk_forward():
    panel = _make_panel(300)
    folds = time_series_cv_folds(panel, n_folds=4)
    assert len(folds) == 4
    for prev, curr in zip(folds, folds[1:]):
        assert len(curr.train_index) > len(prev.train_index)  # expanding window
        assert curr.val_date_range[0] > prev.val_date_range[1]  # walk-forward, non-overlapping


def test_time_series_cv_folds_all_pass_leakage_assertion():
    panel = _make_panel(300)
    for fold in time_series_cv_folds(panel, n_folds=5):
        assert assert_no_chronological_leakage(fold) is True


def test_assert_no_chronological_leakage_catches_a_real_violation():
    from core.ml.cv import CVFold
    from datetime import date

    panel = _make_panel(10, symbols=("AAA.NS",))
    bad_fold = CVFold(
        fold_number=1,
        train_index=panel.index,
        val_index=panel.index,
        train_date_range=(date(2023, 6, 1), date(2023, 6, 10)),
        val_date_range=(date(2023, 6, 5), date(2023, 6, 15)),  # overlaps training range
    )
    with pytest.raises(AssertionError, match="chronological leakage"):
        assert_no_chronological_leakage(bad_fold)


def test_time_series_cv_folds_raises_when_too_few_dates_for_fold_count():
    panel = _make_panel(3)
    with pytest.raises(ValueError, match="Not enough unique dates"):
        time_series_cv_folds(panel, n_folds=10)
