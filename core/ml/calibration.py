"""Phase 2 Step 7: Probability Calibration.

Reuses sklearn's own calibration machinery (`CalibratedClassifierCV` for Platt/sigmoid
and isotonic scaling, `calibration_curve` for reliability curves, `brier_score_loss`)
rather than reimplementing well-established methods. Temperature scaling has no sklearn
equivalent, so it's implemented directly here (a single scalar fit by minimizing
log-loss on a held-out set -- the standard method, Guo et al. 2017).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import brier_score_loss, log_loss

_EPS = 1e-12


def calibrate_platt(fitted_model, X_calib: pd.DataFrame, y_calib: pd.Series):
    """Platt scaling (logistic/sigmoid calibration) via sklearn's own
    `CalibratedClassifierCV(cv="prefit")` -- fits a 1-D logistic regression on top of
    the already-fitted model's outputs, using a separate calibration set (never the
    model's own training data, which would understate real miscalibration)."""
    calibrated = CalibratedClassifierCV(fitted_model, method="sigmoid", cv="prefit")
    calibrated.fit(X_calib, y_calib)
    return calibrated


def calibrate_isotonic(fitted_model, X_calib: pd.DataFrame, y_calib: pd.Series):
    """Isotonic regression calibration -- more flexible than Platt (non-parametric,
    monotonic), but needs more calibration data to avoid overfitting the calibration
    curve itself; both are offered so evidence (Step 12) decides, not an assumption."""
    calibrated = CalibratedClassifierCV(fitted_model, method="isotonic", cv="prefit")
    calibrated.fit(X_calib, y_calib)
    return calibrated


def _apply_temperature(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    """Rescale probabilities by dividing their logits by `temperature` (T=1 is a no-op;
    T>1 softens/flattens overconfident probabilities toward 0.5; T<1 sharpens them)."""
    probabilities = np.clip(probabilities, _EPS, 1 - _EPS)
    logits = np.log(probabilities / (1 - probabilities))
    scaled_logits = logits / temperature
    return 1 / (1 + np.exp(-scaled_logits))


def fit_temperature(y_calib: pd.Series, raw_probabilities: np.ndarray) -> float:
    """Fit the single scalar temperature that minimizes log-loss on the calibration set
    (Guo et al. 2017's temperature scaling). Optimized directly via scalar minimization
    over log-loss rather than gradient descent on a learned linear layer, since a single
    scalar doesn't need it."""

    def neg_log_likelihood(temperature: float) -> float:
        if temperature <= 0:
            return np.inf
        scaled = _apply_temperature(raw_probabilities, temperature)
        return log_loss(y_calib, scaled, labels=[0, 1])

    result = minimize_scalar(neg_log_likelihood, bounds=(0.05, 20.0), method="bounded")
    return float(result.x)


def calibrate_temperature(y_calib: pd.Series, raw_probabilities: np.ndarray) -> tuple[float, np.ndarray]:
    """Fit temperature on the calibration set and return (temperature, calibrated
    probabilities for that same set) -- callers apply `_apply_temperature` with the
    returned `temperature` to any new probabilities (e.g. a held-out test set)."""
    temperature = fit_temperature(y_calib, raw_probabilities)
    return temperature, _apply_temperature(raw_probabilities, temperature)


def compute_ece(y_true: pd.Series, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """Expected Calibration Error: bin predictions by confidence, then take the
    weighted-by-bin-size average gap between each bin's mean predicted probability and
    its actual accuracy (fraction of positives). 0 = perfectly calibrated."""
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.clip(np.digitize(y_prob, bin_edges[1:-1]), 0, n_bins - 1)

    total_error = 0.0
    n = len(y_prob)
    for b in range(n_bins):
        mask = bin_indices == b
        if not mask.any():
            continue
        bin_confidence = y_prob[mask].mean()
        bin_accuracy = y_true[mask].mean()
        total_error += (mask.sum() / n) * abs(bin_accuracy - bin_confidence)
    return float(total_error)


def compute_mce(y_true: pd.Series, y_prob: np.ndarray, n_bins: int = 10) -> float:
    """Maximum Calibration Error: the single worst-calibrated bin's gap, not a weighted
    average -- surfaces the worst-case miscalibration ECE's averaging can hide."""
    y_true = np.asarray(y_true)
    y_prob = np.asarray(y_prob)
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bin_indices = np.clip(np.digitize(y_prob, bin_edges[1:-1]), 0, n_bins - 1)

    max_error = 0.0
    for b in range(n_bins):
        mask = bin_indices == b
        if not mask.any():
            continue
        bin_confidence = y_prob[mask].mean()
        bin_accuracy = y_true[mask].mean()
        max_error = max(max_error, abs(bin_accuracy - bin_confidence))
    return float(max_error)


def compute_calibration_curve(y_true: pd.Series, y_prob: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Reliability curve: mean predicted probability vs. actual fraction of positives,
    per bin -- wraps sklearn's own `calibration_curve` in a DataFrame for convenient
    reporting rather than reimplementing the binning."""
    fraction_of_positives, mean_predicted_value = calibration_curve(y_true, y_prob, n_bins=n_bins, strategy="uniform")
    return pd.DataFrame({"mean_predicted_probability": mean_predicted_value, "fraction_of_positives": fraction_of_positives})


@dataclass
class CalibrationMethodResult:
    method: str
    brier_score: float
    ece: float
    mce: float
    calibration_curve: pd.DataFrame


@dataclass
class CalibrationComparisonReport:
    results: list[CalibrationMethodResult]
    temperature: float | None = None

    def best_by_brier(self) -> CalibrationMethodResult:
        return min(self.results, key=lambda r: r.brier_score)

    def best_by_ece(self) -> CalibrationMethodResult:
        return min(self.results, key=lambda r: r.ece)


def compare_calibration_methods(
    fitted_model, X_calib: pd.DataFrame, y_calib: pd.Series, X_test: pd.DataFrame, y_test: pd.Series, n_bins: int = 10,
) -> CalibrationComparisonReport:
    """Compare raw (uncalibrated) probabilities against Platt, isotonic, and
    temperature scaling -- all fit on the same calibration set, all measured on the same
    held-out test set, so the comparison is apples-to-apples. Selection is left to the
    caller (Step 12's benchmark suite decides on evidence, not this function).
    """
    raw_calib_proba = fitted_model.predict_proba(X_calib)[:, 1]
    raw_test_proba = fitted_model.predict_proba(X_test)[:, 1]

    results = [
        CalibrationMethodResult(
            method="raw",
            brier_score=float(brier_score_loss(y_test, raw_test_proba)),
            ece=compute_ece(y_test, raw_test_proba, n_bins),
            mce=compute_mce(y_test, raw_test_proba, n_bins),
            calibration_curve=compute_calibration_curve(y_test, raw_test_proba, n_bins),
        )
    ]

    platt_model = calibrate_platt(fitted_model, X_calib, y_calib)
    platt_test_proba = platt_model.predict_proba(X_test)[:, 1]
    results.append(
        CalibrationMethodResult(
            method="platt",
            brier_score=float(brier_score_loss(y_test, platt_test_proba)),
            ece=compute_ece(y_test, platt_test_proba, n_bins),
            mce=compute_mce(y_test, platt_test_proba, n_bins),
            calibration_curve=compute_calibration_curve(y_test, platt_test_proba, n_bins),
        )
    )

    isotonic_model = calibrate_isotonic(fitted_model, X_calib, y_calib)
    isotonic_test_proba = isotonic_model.predict_proba(X_test)[:, 1]
    results.append(
        CalibrationMethodResult(
            method="isotonic",
            brier_score=float(brier_score_loss(y_test, isotonic_test_proba)),
            ece=compute_ece(y_test, isotonic_test_proba, n_bins),
            mce=compute_mce(y_test, isotonic_test_proba, n_bins),
            calibration_curve=compute_calibration_curve(y_test, isotonic_test_proba, n_bins),
        )
    )

    temperature = fit_temperature(y_calib, raw_calib_proba)
    temp_test_proba = _apply_temperature(raw_test_proba, temperature)
    results.append(
        CalibrationMethodResult(
            method="temperature",
            brier_score=float(brier_score_loss(y_test, temp_test_proba)),
            ece=compute_ece(y_test, temp_test_proba, n_bins),
            mce=compute_mce(y_test, temp_test_proba, n_bins),
            calibration_curve=compute_calibration_curve(y_test, temp_test_proba, n_bins),
        )
    )

    return CalibrationComparisonReport(results=results, temperature=temperature)
