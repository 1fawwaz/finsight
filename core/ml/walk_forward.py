"""Phase 2 Step 8: Walk-Forward Validation.

Both fold styles the directive asks for already exist in the repository under
different names, found via reconnaissance rather than assumed absent:
- Expanding-window: `core.ml.cv.time_series_cv_folds` (Phase 3).
- Rolling-window (fixed-size training window sliding forward): `core.backtester
  .walk_forward_backtest` (original spec Phase 5) already does exactly this.

This module reuses both directly for actually running validation across multiple
window configurations, and adds the one piece that didn't exist: an explicit,
inspectable leakage-verification *report* -- the directive's "verify leakage prevention
explicitly, not by assertion" is read literally here. `assert_no_chronological_leakage`
(Phase 3) already exists and is reused, but a bare `assert` is a silent pass/fail with no
retained evidence (and Python assertions can be stripped entirely with `-O`). Every fold
this module touches gets a persisted, human-inspectable record of exactly what was
checked and what the actual dates were -- proof, not just a runtime check that happened
not to raise.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.backtester import BacktestResult, walk_forward_backtest
from core.ml.cv import CVFold, assert_no_chronological_leakage, time_series_cv_folds


def rolling_window_folds(features: pd.DataFrame, train_window: int, test_window: int) -> list[CVFold]:
    """Fold *boundaries* for a fixed-size rolling training window, mirroring exactly
    the split logic `core.backtester.walk_forward_backtest` trains on -- exposed
    separately (as boundaries only, no model fit) so those boundaries can be fed to the
    same leakage-verification report expanding-window folds use, without duplicating
    the actual training/prediction loop that already lives in the backtester.
    """
    n = len(features)
    dates = features.index
    folds: list[CVFold] = []
    fold_number = 1
    i = train_window
    while i + test_window <= n:
        train_index = features.index[i - train_window : i]
        val_index = features.index[i : i + test_window]
        folds.append(
            CVFold(
                fold_number=fold_number,
                train_index=train_index,
                val_index=val_index,
                train_date_range=(dates[i - train_window], dates[i - 1]),
                val_date_range=(dates[i], dates[min(i + test_window, n) - 1]),
            )
        )
        fold_number += 1
        i += test_window
    return folds


@dataclass
class LeakageCheckRow:
    fold_number: int
    style: str  # "expanding" or "rolling"
    train_start: object
    train_end: object
    val_start: object
    val_end: object
    gap_days: int
    passed: bool
    failure_reason: str | None = None


def verify_no_leakage_report(folds: list[CVFold], style: str) -> pd.DataFrame:
    """Explicit, inspectable evidence for every fold -- not a bare assert. Reuses
    `assert_no_chronological_leakage`'s own check (val_min > train_max) but catches its
    AssertionError per-fold rather than letting the first failure abort the whole
    report, so a single bad fold doesn't hide the status of every other fold. Every row
    is retained (pass or fail), which is what makes this a verification *report* rather
    than a pass/fail gate.
    """
    rows = []
    for fold in folds:
        train_end = fold.train_date_range[1]
        val_start = fold.val_date_range[0]
        gap_days = (pd.Timestamp(val_start) - pd.Timestamp(train_end)).days
        try:
            assert_no_chronological_leakage(fold)
            rows.append(
                LeakageCheckRow(
                    fold_number=fold.fold_number, style=style,
                    train_start=fold.train_date_range[0], train_end=train_end,
                    val_start=val_start, val_end=fold.val_date_range[1],
                    gap_days=gap_days, passed=True,
                )
            )
        except AssertionError as exc:
            rows.append(
                LeakageCheckRow(
                    fold_number=fold.fold_number, style=style,
                    train_start=fold.train_date_range[0], train_end=train_end,
                    val_start=val_start, val_end=fold.val_date_range[1],
                    gap_days=gap_days, passed=False, failure_reason=str(exc),
                )
            )
    return pd.DataFrame([vars(r) for r in rows])


@dataclass
class WindowConfigResult:
    style: str
    config: dict
    n_folds: int
    mean_accuracy: float
    mean_precision: float
    mean_recall: float
    leakage_report: pd.DataFrame


def run_rolling_window_validation(
    features: pd.DataFrame, labels: pd.Series, close: pd.Series, window_configs: list[tuple[int, int]],
) -> list[WindowConfigResult]:
    """Run `core.backtester.walk_forward_backtest` (reused, not duplicated) across
    multiple (train_window, test_window) configurations -- "across multiple windows",
    per the directive -- plus an explicit leakage report for each configuration's
    fold boundaries.
    """
    results = []
    for train_window, test_window in window_configs:
        folds = rolling_window_folds(features, train_window, test_window)
        # The report itself is the evidence (every fold's actual dates, pass/fail) --
        # a leakage failure is a Prime-Directive-level problem the caller must act on
        # by inspecting `leakage_report["passed"]`, not something this function decides
        # how to handle on the caller's behalf.
        leakage_report = verify_no_leakage_report(folds, style="rolling")

        try:
            backtest: BacktestResult = walk_forward_backtest(features, labels, close, train_window=train_window, test_window=test_window)
            mean_accuracy, mean_precision, mean_recall = backtest.accuracy, backtest.precision, backtest.recall
        except ValueError:
            mean_accuracy = mean_precision = mean_recall = float("nan")

        results.append(
            WindowConfigResult(
                style="rolling",
                config={"train_window": train_window, "test_window": test_window},
                n_folds=len(folds),
                mean_accuracy=mean_accuracy,
                mean_precision=mean_precision,
                mean_recall=mean_recall,
                leakage_report=leakage_report,
            )
        )
    return results


def run_expanding_window_validation(
    features: pd.DataFrame, labels: pd.Series, n_folds_list: list[int],
) -> list[WindowConfigResult]:
    """Run `core.ml.cv.time_series_cv_folds` (reused, not duplicated) at multiple fold
    counts -- "across multiple windows" for the expanding-window style -- fitting a
    fresh RandomForest per fold (the same model family already used elsewhere in this
    codebase's quick comparisons, e.g. core.ml.labels.compare_label_candidates) and an
    explicit leakage report per configuration.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, precision_score, recall_score

    results = []
    for n_folds in n_folds_list:
        try:
            folds = time_series_cv_folds(features, n_folds=n_folds)
        except ValueError:
            results.append(
                WindowConfigResult(
                    style="expanding", config={"n_folds": n_folds}, n_folds=0,
                    mean_accuracy=float("nan"), mean_precision=float("nan"), mean_recall=float("nan"),
                    leakage_report=pd.DataFrame(),
                )
            )
            continue

        leakage_report = verify_no_leakage_report(folds, style="expanding")

        accuracies, precisions, recalls = [], [], []
        for fold in folds:
            X_train, y_train = features.loc[fold.train_index], labels.loc[fold.train_index]
            X_val, y_val = features.loc[fold.val_index], labels.loc[fold.val_index]
            if y_train.nunique() < 2:
                continue
            model = RandomForestClassifier(n_estimators=100, max_depth=5, min_samples_leaf=10, random_state=42)
            model.fit(X_train, y_train)
            preds = model.predict(X_val)
            accuracies.append(accuracy_score(y_val, preds))
            precisions.append(precision_score(y_val, preds, zero_division=0))
            recalls.append(recall_score(y_val, preds, zero_division=0))

        results.append(
            WindowConfigResult(
                style="expanding",
                config={"n_folds": n_folds},
                n_folds=len(folds),
                mean_accuracy=float(np.mean(accuracies)) if accuracies else float("nan"),
                mean_precision=float(np.mean(precisions)) if precisions else float("nan"),
                mean_recall=float(np.mean(recalls)) if recalls else float("nan"),
                leakage_report=leakage_report,
            )
        )
    return results
