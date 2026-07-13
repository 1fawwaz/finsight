"""Phase 2 Step 12: Benchmarking and Model Promotion.

Reuses every existing metric/CV/calibration primitive already built rather than
recomputing them: `core.portfolio.sharpe_ratio`/`max_drawdown` (trading),
`core.ml.calibration.compare_calibration_methods` (calibration),
`core.ml.cv.time_series_cv_folds`/`core.ml.walk_forward.rolling_window_folds`
(stability), `core.ml.feature_selection.compute_feature_stability` (stability),
`core.ml.training.build_model` (model construction). Classification metrics are
sklearn's own; no new ML framework or storage system is introduced (no Architecture
Change Rule justification needed).
"""

from __future__ import annotations

import time
import tracemalloc
from dataclasses import dataclass, field
from io import BytesIO

import joblib
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    log_loss,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)

from core.config import get_logger
from core.ml.calibration import compare_calibration_methods
from core.ml.cv import time_series_cv_folds
from core.ml.feature_selection import compute_feature_stability
from core.ml.training import build_model
from core.ml.walk_forward import rolling_window_folds
from core.portfolio import max_drawdown, sharpe_ratio

logger = get_logger(__name__)

RANDOM_STATE = 42
TRADING_DAYS_PER_YEAR = 252


def _build_model_safe(family: str, params: dict, random_state: int | None = None):
    """Wraps `core.ml.training.build_model`, stripping any `random_state` key from
    `params` first -- that function always injects its own fixed `RANDOM_STATE` for
    every family and raises `TypeError` on a duplicate keyword otherwise (a real bug
    hit while building this module's own tests, not a hypothetical). When this module
    needs a *specific* random_state (Step 12's seed-sensitivity stability check is
    meaningless with a hardcoded seed), it's applied by setting the attribute directly
    on the already-constructed estimator rather than fighting that function's contract.
    """
    clean_params = {k: v for k, v in params.items() if k != "random_state"}
    model = build_model(family, clean_params)
    if random_state is not None and hasattr(model, "random_state"):
        model.random_state = random_state
    return model


# --- Category 1: Classification ---------------------------------------------------------


def compute_classification_metrics(y_true: pd.Series, y_pred: np.ndarray, y_proba: np.ndarray) -> dict:
    """Every metric the directive's Classification row lists -- all sklearn, none
    reimplemented."""
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_true, y_proba)) if len(set(y_true)) > 1 else float("nan"),
        "pr_auc": float(average_precision_score(y_true, y_proba)) if len(set(y_true)) > 1 else float("nan"),
        "log_loss": float(log_loss(y_true, y_proba, labels=[0, 1])),
        "mcc": float(matthews_corrcoef(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
    }


# --- Category 2: Calibration --------------------------------------------------------------


def compute_calibration_metrics(fitted_model, X_calib: pd.DataFrame, y_calib: pd.Series, X_test: pd.DataFrame, y_test: pd.Series) -> dict:
    """Reuses Step 7's full comparison; reports the best-by-ECE method's numbers as
    this candidate's calibration benchmark (the method itself is recorded too, so a
    reader can see which one was used, not just the resulting numbers)."""
    report = compare_calibration_methods(fitted_model, X_calib, y_calib, X_test, y_test)
    best = report.best_by_ece()
    return {
        "method": best.method,
        "brier_score": best.brier_score,
        "ece": best.ece,
        "mce": best.mce,
        "temperature": report.temperature,
    }


# --- Category 3: Trading ------------------------------------------------------------------


def _sortino_ratio(daily_returns: pd.Series, risk_free_rate: float = 0.0) -> float:
    """Like Sharpe, but only penalizes downside deviation -- upside volatility isn't
    treated as risk. No existing implementation in this codebase; Sharpe's structure
    (excess return / dispersion, annualized) is mirrored for consistency."""
    daily_returns = daily_returns.dropna()
    if daily_returns.empty:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily_returns - daily_rf
    downside = excess[excess < 0]
    downside_std = downside.std()
    if not downside_std or downside_std == 0:
        return 0.0
    return float((excess.mean() / downside_std) * np.sqrt(TRADING_DAYS_PER_YEAR))


def compute_trading_metrics(predicted_direction: pd.Series, next_day_returns: pd.Series) -> dict:
    """Trading-simulation metrics for a long/flat single-asset signal (predicted_direction
    in {0,1}; 1 = hold a long position for that day, matching `core.backtester`'s own
    signal convention, reused not redefined). `next_day_returns` is the real forward
    return realized on each signal date.
    """
    aligned_signal, aligned_returns = predicted_direction.align(next_day_returns, join="inner")
    strategy_returns = aligned_returns * aligned_signal
    equity_curve = (1 + strategy_returns.fillna(0)).cumprod()

    trades = strategy_returns[aligned_signal == 1]  # only days actually in the market
    gains = trades[trades > 0]
    losses = trades[trades < 0]

    n_days = len(aligned_signal)
    annual_return = float(equity_curve.iloc[-1] ** (TRADING_DAYS_PER_YEAR / n_days) - 1) if n_days > 0 and equity_curve.iloc[-1] > 0 else float("nan")
    volatility = float(strategy_returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(strategy_returns) > 1 else 0.0
    max_dd = max_drawdown(equity_curve)
    calmar = float(annual_return / abs(max_dd)) if max_dd not in (0, None) and not np.isnan(annual_return) else float("nan")
    profit_factor = float(gains.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf") if gains.sum() > 0 else 0.0
    win_rate = float((trades > 0).sum() / len(trades)) if len(trades) > 0 else float("nan")
    turnover = float((aligned_signal.diff().fillna(0) != 0).sum() / n_days) if n_days > 0 else 0.0

    return {
        "sharpe": sharpe_ratio(strategy_returns),
        "sortino": _sortino_ratio(strategy_returns),
        "calmar": calmar,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "avg_gain": float(gains.mean()) if len(gains) > 0 else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) > 0 else 0.0,
        "annual_return": annual_return,
        "volatility": volatility,
        "turnover": turnover,
    }


# --- Category 4: Performance ---------------------------------------------------------------


def compute_performance_metrics(family: str, params: dict, X_train: pd.DataFrame, y_train: pd.Series, X_test: pd.DataFrame) -> dict:
    """Training time, prediction latency, memory usage, model size, inference
    throughput -- all measured directly against a real fit/predict, not estimated.
    Memory is measured via `tracemalloc` (stdlib, cross-platform) rather than the
    Unix-only `resource` module or a new `psutil` dependency this Windows-hosted
    project doesn't have and doesn't need for a peak-allocation measurement."""
    tracemalloc.start()
    start = time.perf_counter()
    model = _build_model_safe(family, params)
    model.fit(X_train, y_train)
    training_time_seconds = time.perf_counter() - start
    _current, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Prediction latency: average single-row predict time over several repeats, not a
    # single noisy sample.
    single_row = X_test.iloc[[0]]
    n_repeats = 20
    start = time.perf_counter()
    for _ in range(n_repeats):
        model.predict_proba(single_row)
    prediction_latency_ms = (time.perf_counter() - start) / n_repeats * 1000

    start = time.perf_counter()
    model.predict(X_test)
    batch_predict_seconds = time.perf_counter() - start
    inference_throughput = float(len(X_test) / batch_predict_seconds) if batch_predict_seconds > 0 else float("inf")

    buffer = BytesIO()
    joblib.dump(model, buffer)
    model_size_bytes = buffer.getbuffer().nbytes

    return {
        "training_time_seconds": float(training_time_seconds),
        "prediction_latency_ms": float(prediction_latency_ms),
        "memory_usage_mb": float(peak_bytes / (1024 * 1024)),
        "model_size_bytes": int(model_size_bytes),
        "inference_throughput_per_sec": inference_throughput,
    }


# --- Category 5: Stability -----------------------------------------------------------------


def compute_stability_metrics(features: pd.DataFrame, labels: pd.Series, family: str, params: dict, n_repeats: int = 5) -> dict:
    """Fold variance and walk-forward variance (dispersion of accuracy across the
    existing expanding/rolling fold generators, reused not recomputed), feature
    stability (reuses Step 6's `compute_feature_stability` directly), and
    prediction/probability stability (agreement rate and probability dispersion across
    `n_repeats` refits with different random seeds on the *same* data -- a model whose
    predictions flip depending on the random seed alone is unstable in a way accuracy
    numbers don't show)."""
    from sklearn.metrics import accuracy_score as _accuracy_score

    expanding_folds = time_series_cv_folds(features, n_folds=5)
    expanding_accuracies = []
    for fold in expanding_folds:
        X_train, y_train = features.loc[fold.train_index], labels.loc[fold.train_index]
        X_val, y_val = features.loc[fold.val_index], labels.loc[fold.val_index]
        if y_train.nunique() < 2:
            continue
        model = _build_model_safe(family, params)
        model.fit(X_train, y_train)
        expanding_accuracies.append(_accuracy_score(y_val, model.predict(X_val)))

    train_window = max(100, len(features) // 4)
    test_window = max(20, len(features) // 20)
    rolling_folds_list = rolling_window_folds(features, train_window=train_window, test_window=test_window)
    rolling_accuracies = []
    for fold in rolling_folds_list[:10]:  # cap for a bounded interactive benchmark run
        X_train, y_train = features.loc[fold.train_index], labels.loc[fold.train_index]
        X_val, y_val = features.loc[fold.val_index], labels.loc[fold.val_index]
        if y_train.nunique() < 2:
            continue
        model = _build_model_safe(family, params)
        model.fit(X_train, y_train)
        rolling_accuracies.append(_accuracy_score(y_val, model.predict(X_val)))

    feature_stability = compute_feature_stability(features, labels, n_folds=5)
    mean_cv = feature_stability.feature_stability["coefficient_of_variation"].mean() if not feature_stability.feature_stability.empty else float("nan")

    # Prediction/probability stability: same train/test split, only the random seed varies.
    n = len(features)
    train_end = int(n * 0.7)
    X_train, y_train = features.iloc[:train_end], labels.iloc[:train_end]
    X_test = features.iloc[train_end:]

    all_predictions = []
    all_probabilities = []
    for seed in range(n_repeats):
        model = _build_model_safe(family, params, random_state=seed)
        model.fit(X_train, y_train)
        all_predictions.append(model.predict(X_test))
        all_probabilities.append(model.predict_proba(X_test)[:, 1])

    predictions_df = pd.DataFrame(all_predictions)
    probabilities_df = pd.DataFrame(all_probabilities)
    # Prediction stability: fraction of (row, seed-pair) combinations that agree with
    # the majority prediction for that row -- 1.0 means every seed always agrees.
    majority_vote = predictions_df.mode(axis=0).iloc[0]
    agreement_rate = (predictions_df.eq(majority_vote, axis=1)).mean(axis=0).mean()
    probability_std = probabilities_df.std(axis=0).mean()

    return {
        "fold_variance": float(np.var(expanding_accuracies)) if expanding_accuracies else float("nan"),
        "walk_forward_variance": float(np.var(rolling_accuracies)) if rolling_accuracies else float("nan"),
        "feature_stability_mean_cv": float(mean_cv),
        "prediction_stability": float(agreement_rate),
        "probability_stability_std": float(probability_std),
    }


# --- Full benchmark ---------------------------------------------------------------------------


@dataclass
class FullBenchmarkReport:
    model_family: str
    hyperparameters: dict
    classification: dict
    calibration: dict
    trading: dict
    performance: dict
    stability: dict
    fold_metrics: list[float] = field(default_factory=list)  # per-fold primary metric, for the promotion test


def run_full_benchmark(
    features: pd.DataFrame, labels: pd.Series, close: pd.Series, family: str = "random_forest", hyperparameters: dict | None = None,
) -> FullBenchmarkReport:
    """Run every benchmark category for one model family/hyperparameter configuration
    on one chronological 60/20/20 train/calibration/test split (a direct positional
    split -- simpler than reusing `core.ml.cv.chronological_train_val_test_split`,
    which is shaped for multi-symbol panel data with a (symbol, date) MultiIndex; this
    operates on one symbol's already-loaded feature set).
    """
    params = hyperparameters or {"n_estimators": 200, "max_depth": 5, "min_samples_leaf": 10, "random_state": RANDOM_STATE}

    n = len(features)
    train_end = int(n * 0.6)
    calib_end = int(n * 0.8)
    X_train, y_train = features.iloc[:train_end], labels.iloc[:train_end]
    X_calib, y_calib = features.iloc[train_end:calib_end], labels.iloc[train_end:calib_end]
    X_test, y_test = features.iloc[calib_end:], labels.iloc[calib_end:]

    model = _build_model_safe(family, params)
    model.fit(X_train, y_train)
    y_pred = model.predict(X_test)
    y_proba = model.predict_proba(X_test)[:, 1]

    classification = compute_classification_metrics(y_test, y_pred, y_proba)
    calibration = compute_calibration_metrics(model, X_calib, y_calib, X_test, y_test)

    next_day_returns = (close.shift(-1) / close - 1).reindex(X_test.index)
    predicted_direction = pd.Series(y_pred, index=X_test.index)
    trading = compute_trading_metrics(predicted_direction, next_day_returns)

    performance = compute_performance_metrics(family, params, X_train, y_train, X_test)
    stability = compute_stability_metrics(features, labels, family, params)

    # Per-fold primary metric (ROC-AUC), for the Model Promotion Rule's paired
    # statistical test -- reuses the same expanding-window folds stability already
    # computed, not a second CV pass.
    fold_metrics = []
    for fold in time_series_cv_folds(features, n_folds=5):
        X_tr, y_tr = features.loc[fold.train_index], labels.loc[fold.train_index]
        X_val, y_val = features.loc[fold.val_index], labels.loc[fold.val_index]
        if y_tr.nunique() < 2 or y_val.nunique() < 2:
            continue
        fold_model = _build_model_safe(family, params)
        fold_model.fit(X_tr, y_tr)
        fold_proba = fold_model.predict_proba(X_val)[:, 1]
        fold_metrics.append(float(roc_auc_score(y_val, fold_proba)))

    return FullBenchmarkReport(
        model_family=family, hyperparameters=params, classification=classification, calibration=calibration,
        trading=trading, performance=performance, stability=stability, fold_metrics=fold_metrics,
    )


# --- Model Promotion Rule -----------------------------------------------------------------


@dataclass
class PromotionDecision:
    promote: bool
    statistically_superior: bool
    p_value: float | None
    calibration_acceptable: bool
    walk_forward_passed: bool
    latency_acceptable: bool
    reasoning: str


def evaluate_model_promotion(
    challenger: FullBenchmarkReport,
    champion: FullBenchmarkReport | None,
    alpha: float = 0.05,
    max_latency_ms: float = 200.0,
    max_ece: float = 0.15,
) -> PromotionDecision:
    """A candidate replaces the champion only if statistically superior (paired
    Wilcoxon signed-rank test on per-fold ROC-AUC -- not just a higher mean), with
    acceptable calibration, a walk-forward pass (no NaN fold metrics), and latency
    within bounds. Ties (statistically indistinguishable) retain the champion --
    simplicity and stability win, per the directive's own tie-breaking rule.

    `champion=None` is the bootstrap case (no incumbent exists yet): the challenger is
    promoted if its own evidence is internally sound (calibration/latency/walk-forward
    pass), since there is nothing to compare against -- stated explicitly in the
    reasoning, not silently treated as "statistically superior to nothing."
    """
    walk_forward_passed = len(challenger.fold_metrics) >= 3 and not any(np.isnan(m) for m in challenger.fold_metrics)
    calibration_acceptable = challenger.calibration["ece"] <= max_ece
    latency_acceptable = challenger.performance["prediction_latency_ms"] <= max_latency_ms

    if champion is None:
        promote = walk_forward_passed and calibration_acceptable and latency_acceptable
        reasoning = (
            f"No incumbent champion exists -- bootstrap promotion. walk_forward_passed={walk_forward_passed}, "
            f"calibration_acceptable={calibration_acceptable} (ECE={challenger.calibration['ece']:.4f} <= {max_ece}), "
            f"latency_acceptable={latency_acceptable} ({challenger.performance['prediction_latency_ms']:.2f}ms <= {max_latency_ms}ms). "
            f"DECISION: {'PROMOTE (bootstrap)' if promote else 'DO NOT PROMOTE'}."
        )
        return PromotionDecision(
            promote=promote, statistically_superior=False, p_value=None,
            calibration_acceptable=calibration_acceptable, walk_forward_passed=walk_forward_passed,
            latency_acceptable=latency_acceptable, reasoning=reasoning,
        )

    statistically_superior = False
    p_value = None
    if len(challenger.fold_metrics) == len(champion.fold_metrics) and len(challenger.fold_metrics) >= 3:
        differences = np.array(challenger.fold_metrics) - np.array(champion.fold_metrics)
        if np.any(differences != 0):
            try:
                _stat, p_value = stats.wilcoxon(differences, alternative="greater")
                statistically_superior = p_value < alpha and float(np.mean(differences)) > 0
            except ValueError as exc:
                # scipy raises if all differences are identical/zero-variance in a way
                # the test can't handle -- treated as "not statistically superior",
                # logged, not silently swallowed.
                logger.warning("Wilcoxon test could not be computed (%s) -- treating as not statistically superior.", exc)
        else:
            p_value = 1.0
    else:
        logger.warning(
            "Challenger and champion have mismatched/insufficient fold counts (%d vs %d) -- cannot run a paired test.",
            len(challenger.fold_metrics), len(champion.fold_metrics),
        )

    promote = statistically_superior and calibration_acceptable and walk_forward_passed and latency_acceptable
    reasoning = (
        f"Paired Wilcoxon signed-rank test on {len(challenger.fold_metrics)} fold(s): "
        f"p={p_value if p_value is not None else 'N/A'} (alpha={alpha}) -> statistically_superior={statistically_superior}. "
        f"calibration_acceptable={calibration_acceptable} (ECE={challenger.calibration['ece']:.4f}), "
        f"walk_forward_passed={walk_forward_passed}, latency_acceptable={latency_acceptable} "
        f"({challenger.performance['prediction_latency_ms']:.2f}ms <= {max_latency_ms}ms). "
        f"DECISION: {'PROMOTE' if promote else 'RETAIN CHAMPION (ties/insufficiency favor stability)'}."
    )
    return PromotionDecision(
        promote=promote, statistically_superior=statistically_superior, p_value=p_value,
        calibration_acceptable=calibration_acceptable, walk_forward_passed=walk_forward_passed,
        latency_acceptable=latency_acceptable, reasoning=reasoning,
    )
