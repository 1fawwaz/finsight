"""Tests for core.ml.benchmark: Phase 2 Step 12 (Benchmarking and Model Promotion)."""

import numpy as np
import pandas as pd
import pytest

from core.ml.benchmark import (
    FullBenchmarkReport,
    compute_classification_metrics,
    compute_performance_metrics,
    compute_stability_metrics,
    compute_trading_metrics,
    evaluate_model_promotion,
    run_full_benchmark,
)


def _make_dataset(n: int = 900, seed: int = 0):
    rng = np.random.default_rng(seed)
    index = pd.date_range("2021-01-01", periods=n, freq="D", name="date")
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=index).clip(lower=1)
    features = pd.DataFrame(
        {"f1": rng.normal(0, 1, n), "f2": rng.normal(0, 1, n), "f3": rng.uniform(-1, 1, n)}, index=index
    )
    labels = pd.Series((close.shift(-1) > close).astype(float), index=index).iloc[:-1]
    features, close = features.iloc[:-1], close.iloc[:-1]
    return features, labels.astype(int), close


# --- Classification ---------------------------------------------------------------------


def test_classification_metrics_perfect_predictions():
    y_true = pd.Series([1, 0, 1, 0, 1])
    y_pred = np.array([1, 0, 1, 0, 1])
    y_proba = np.array([0.9, 0.1, 0.9, 0.1, 0.9])
    metrics = compute_classification_metrics(y_true, y_pred, y_proba)
    assert metrics["accuracy"] == 1.0
    assert metrics["mcc"] == 1.0
    assert metrics["roc_auc"] == 1.0


def test_classification_metrics_all_keys_present():
    y_true = pd.Series([1, 0, 1, 0])
    y_pred = np.array([1, 1, 0, 0])
    y_proba = np.array([0.6, 0.55, 0.4, 0.3])
    metrics = compute_classification_metrics(y_true, y_pred, y_proba)
    for key in ("accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc", "log_loss", "mcc", "balanced_accuracy"):
        assert key in metrics


# --- Trading -----------------------------------------------------------------------------


def test_trading_metrics_all_flat_produces_zero_activity():
    index = pd.date_range("2023-01-01", periods=50, freq="D")
    signal = pd.Series(0, index=index)  # never in the market
    returns = pd.Series(np.random.default_rng(1).normal(0, 0.01, 50), index=index)
    metrics = compute_trading_metrics(signal, returns)
    assert metrics["win_rate"] != metrics["win_rate"] or pd.isna(metrics["win_rate"])  # no trades -> NaN, not fabricated
    assert metrics["turnover"] == 0.0


def test_trading_metrics_always_long_matches_buy_and_hold_return():
    index = pd.date_range("2023-01-01", periods=50, freq="D")
    rng = np.random.default_rng(2)
    returns = pd.Series(rng.normal(0.001, 0.01, 50), index=index)
    signal = pd.Series(1, index=index)  # always long
    metrics = compute_trading_metrics(signal, returns)
    expected_equity = (1 + returns).cumprod().iloc[-1]
    # annual_return implies the same terminal equity when back-computed over n days.
    implied_equity = (1 + metrics["annual_return"]) ** (len(returns) / 252)
    assert implied_equity == pytest.approx(expected_equity, rel=1e-6)


def test_trading_metrics_profit_factor_reflects_gains_vs_losses():
    index = pd.date_range("2023-01-01", periods=4, freq="D")
    signal = pd.Series([1, 1, 1, 1], index=index)
    returns = pd.Series([0.02, -0.01, 0.03, -0.01], index=index)  # gains sum=0.05, losses sum=-0.02
    metrics = compute_trading_metrics(signal, returns)
    assert metrics["profit_factor"] == pytest.approx(0.05 / 0.02)


def test_trading_metrics_turnover_counts_signal_changes():
    index = pd.date_range("2023-01-01", periods=6, freq="D")
    signal = pd.Series([1, 1, 0, 0, 1, 1], index=index)  # 2 changes over 6 days
    returns = pd.Series([0.01] * 6, index=index)
    metrics = compute_trading_metrics(signal, returns)
    assert metrics["turnover"] == pytest.approx(2 / 6)


# --- Performance -------------------------------------------------------------------------


def test_performance_metrics_measures_real_positive_values():
    features, labels, _ = _make_dataset(300)
    X_train, y_train = features.iloc[:200], labels.iloc[:200]
    X_test = features.iloc[200:]
    metrics = compute_performance_metrics("random_forest", {"n_estimators": 50, "max_depth": 3, "random_state": 42}, X_train, y_train, X_test)

    assert metrics["training_time_seconds"] > 0
    assert metrics["prediction_latency_ms"] > 0
    assert metrics["memory_usage_mb"] >= 0
    assert metrics["model_size_bytes"] > 0
    assert metrics["inference_throughput_per_sec"] > 0


# --- Stability ---------------------------------------------------------------------------


def test_stability_metrics_returns_all_expected_keys():
    features, labels, _ = _make_dataset(900)
    metrics = compute_stability_metrics(features, labels, "random_forest", {"n_estimators": 50, "max_depth": 3}, n_repeats=3)
    for key in ("fold_variance", "walk_forward_variance", "feature_stability_mean_cv", "prediction_stability", "probability_stability_std"):
        assert key in metrics


def test_stability_prediction_stability_is_1_for_a_deterministic_model():
    """A model with no randomness at all (e.g. max_depth=1 decision stump-like RF with
    a single, dominant feature) should show very high prediction agreement across
    reruns with different seeds, since the seed barely matters when the signal is
    strong and the model is simple."""
    features, labels, _ = _make_dataset(900, seed=5)
    metrics = compute_stability_metrics(features, labels, "random_forest", {"n_estimators": 10, "max_depth": 2}, n_repeats=3)
    assert 0.0 <= metrics["prediction_stability"] <= 1.0


# --- Full benchmark + promotion ------------------------------------------------------------


def test_run_full_benchmark_produces_all_five_categories():
    features, labels, close = _make_dataset(900)
    report = run_full_benchmark(features, labels, close, family="random_forest", hyperparameters={"n_estimators": 50, "max_depth": 3, "random_state": 42})

    assert report.model_family == "random_forest"
    assert set(report.classification.keys()) >= {"accuracy", "roc_auc", "mcc"}
    assert set(report.calibration.keys()) >= {"ece", "brier_score"}
    assert set(report.trading.keys()) >= {"sharpe", "sortino", "max_drawdown"}
    assert set(report.performance.keys()) >= {"training_time_seconds", "prediction_latency_ms"}
    assert set(report.stability.keys()) >= {"fold_variance", "prediction_stability"}
    assert len(report.fold_metrics) >= 3


def test_promotion_bootstrap_case_no_champion_exists():
    features, labels, close = _make_dataset(900)
    challenger = run_full_benchmark(features, labels, close, hyperparameters={"n_estimators": 50, "max_depth": 3, "random_state": 42})

    decision = evaluate_model_promotion(challenger, champion=None)

    assert decision.p_value is None
    assert decision.statistically_superior is False
    assert "bootstrap" in decision.reasoning.lower()


def test_promotion_retains_champion_when_fold_metrics_are_identical():
    """Identical fold metrics (challenger == champion) must never be treated as
    statistically superior -- a tie favors the incumbent."""
    fake_report_kwargs = dict(
        model_family="random_forest", hyperparameters={},
        classification={}, calibration={"ece": 0.05}, trading={},
        performance={"prediction_latency_ms": 5.0}, stability={},
    )
    champion = FullBenchmarkReport(fold_metrics=[0.5, 0.51, 0.49, 0.52, 0.50], **fake_report_kwargs)
    challenger = FullBenchmarkReport(fold_metrics=[0.5, 0.51, 0.49, 0.52, 0.50], **fake_report_kwargs)

    decision = evaluate_model_promotion(challenger, champion)

    assert decision.promote is False
    assert decision.statistically_superior is False


def test_promotion_promotes_a_clearly_superior_challenger():
    fake_kwargs = dict(model_family="random_forest", hyperparameters={}, classification={}, trading={})
    champion = FullBenchmarkReport(
        fold_metrics=[0.50, 0.49, 0.51, 0.50, 0.48],
        calibration={"ece": 0.05}, performance={"prediction_latency_ms": 5.0}, stability={}, **fake_kwargs,
    )
    challenger = FullBenchmarkReport(
        fold_metrics=[0.65, 0.63, 0.66, 0.64, 0.67],  # consistently, meaningfully higher every fold
        calibration={"ece": 0.04}, performance={"prediction_latency_ms": 5.0}, stability={}, **fake_kwargs,
    )

    decision = evaluate_model_promotion(challenger, champion)

    assert decision.statistically_superior is True
    assert decision.promote is True
    assert decision.p_value < 0.05


def test_promotion_rejects_when_latency_exceeds_bound_despite_superior_accuracy():
    fake_kwargs = dict(model_family="random_forest", hyperparameters={}, classification={}, trading={})
    champion = FullBenchmarkReport(
        fold_metrics=[0.50, 0.49, 0.51, 0.50, 0.48], calibration={"ece": 0.05},
        performance={"prediction_latency_ms": 5.0}, stability={}, **fake_kwargs,
    )
    slow_challenger = FullBenchmarkReport(
        fold_metrics=[0.65, 0.63, 0.66, 0.64, 0.67], calibration={"ece": 0.04},
        performance={"prediction_latency_ms": 500.0}, stability={}, **fake_kwargs,  # way too slow
    )

    decision = evaluate_model_promotion(slow_challenger, champion, max_latency_ms=200.0)

    assert decision.statistically_superior is True  # accuracy alone would promote it
    assert decision.latency_acceptable is False
    assert decision.promote is False  # but latency gate blocks promotion


def test_promotion_rejects_when_calibration_is_unacceptable():
    fake_kwargs = dict(model_family="random_forest", hyperparameters={}, classification={}, trading={})
    champion = FullBenchmarkReport(
        fold_metrics=[0.50, 0.49, 0.51, 0.50, 0.48], calibration={"ece": 0.05},
        performance={"prediction_latency_ms": 5.0}, stability={}, **fake_kwargs,
    )
    badly_calibrated_challenger = FullBenchmarkReport(
        fold_metrics=[0.65, 0.63, 0.66, 0.64, 0.67], calibration={"ece": 0.40},  # very poorly calibrated
        performance={"prediction_latency_ms": 5.0}, stability={}, **fake_kwargs,
    )

    decision = evaluate_model_promotion(badly_calibrated_challenger, champion, max_ece=0.15)

    assert decision.calibration_acceptable is False
    assert decision.promote is False
