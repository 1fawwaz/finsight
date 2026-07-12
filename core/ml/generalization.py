"""Phase 3 Step 2.3.1: the mandatory Generalization Policy gate -- overfitting,
underfitting, and fold-instability checks with evidence, applied to every candidate
model before it's eligible for the registry. No model enters the registry on a high
score alone.

Thresholds: this codebase defines no prior MLOps thresholds (no config entry, doc, or
existing standard specifies acceptable overfit/underfit/instability bounds for
FinSight's ML pipeline -- checked core/config.py and docs before assuming this), so the
spec's stated defaults are used, and that absence is recorded explicitly via
THRESHOLD_SOURCE rather than silently assumed.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score

from core.config import get_logger
from core.ml.baseline import naive_baseline_metrics
from core.ml.training import TARGET_METRIC, build_model, fit_with_early_stopping

logger = get_logger(__name__)

OVERFIT_GAP_THRESHOLD_PCT = 20.0
UNDERFIT_BASELINE_MARGIN_PCT = 5.0
FOLD_INSTABILITY_THRESHOLD_PCT = 15.0
THRESHOLD_SOURCE = "spec default -- no project-defined MLOps threshold found in this codebase"

# A daily-equity-direction target is structurally noisy (near-efficient-market signal),
# so a wide relative gap on an already-tiny absolute ROC-AUC delta (e.g. 0.52 -> 0.48 is
# only a 0.04 absolute move but a large relative %) is expected, not necessarily a sign
# of true overfitting the way it would be for a target with real learnable structure.
# Documented here, not applied silently -- the raw 20%-relative default is still what's
# checked in evaluate_generalization(); this note explains why a flagged model here
# should be read in that light before assuming the model itself is broken.
NOISY_TARGET_CAVEAT = (
    "This target (next-session equity direction) is close to a random walk at the "
    "daily granularity used here, so small absolute metric changes can look like large "
    "relative gaps -- a flag here should be interpreted alongside the absolute numbers, "
    "not the percentage alone."
)


@dataclass
class GateResult:
    family: str
    train_metrics: dict
    val_metrics: dict
    test_metrics: dict
    baseline_metrics: dict
    train_val_gap_pct: float
    fold_std_pct: float
    overfit_flag: bool
    underfit_flag: bool
    instability_flag: bool
    passed: bool
    reasoning: str


def _score(model, X: pd.DataFrame, y: pd.Series) -> dict:
    preds = model.predict(X)
    proba = model.predict_proba(X)[:, 1]
    return {
        "accuracy": float(accuracy_score(y, preds)),
        "precision": float(precision_score(y, preds, zero_division=0)),
        "recall": float(recall_score(y, preds, zero_division=0)),
        "f1": float(f1_score(y, preds, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, proba)) if len(set(y)) > 1 else float("nan"),
    }


def evaluate_generalization(
    family: str,
    params: dict,
    fold_mean_metrics: dict,
    fold_std_metrics: dict,
    train_X: pd.DataFrame,
    train_y: pd.Series,
    val_X: pd.DataFrame,
    val_y: pd.Series,
    test_X: pd.DataFrame,
    test_y: pd.Series,
) -> GateResult:
    """Retrain `family`/`params` on the full train split, evaluate on train/val/test,
    and check all three generalization flags with real numbers as evidence. Test is
    used here only for final evaluation, never to pick between candidates."""
    model = build_model(family, params)
    model = fit_with_early_stopping(family, model, train_X, train_y, val_X, val_y)

    train_metrics = _score(model, train_X, train_y)
    val_metrics = _score(model, val_X, val_y)
    test_metrics = _score(model, test_X, test_y)

    train_target = train_metrics[TARGET_METRIC]
    val_target = val_metrics[TARGET_METRIC]
    gap_pct = abs(train_target - val_target) / train_target * 100 if train_target else float("inf")

    fold_std = fold_std_metrics[TARGET_METRIC]
    fold_mean = fold_mean_metrics[TARGET_METRIC]
    fold_std_pct = (fold_std / fold_mean * 100) if fold_mean else float("inf")

    baseline = naive_baseline_metrics(pd.concat([train_X, val_X]), pd.concat([train_y, val_y]))
    baseline_accuracy = baseline["accuracy"]
    train_beats_pct = (train_metrics["accuracy"] - baseline_accuracy) / baseline_accuracy * 100 if baseline_accuracy else 0.0
    val_beats_pct = (val_metrics["accuracy"] - baseline_accuracy) / baseline_accuracy * 100 if baseline_accuracy else 0.0

    overfit = gap_pct > OVERFIT_GAP_THRESHOLD_PCT
    underfit = train_beats_pct < UNDERFIT_BASELINE_MARGIN_PCT and val_beats_pct < UNDERFIT_BASELINE_MARGIN_PCT
    instability = fold_std_pct > FOLD_INSTABILITY_THRESHOLD_PCT
    passed = not (overfit or underfit or instability)

    reasoning = (
        f"{family}: train {TARGET_METRIC}={train_target:.4f}, val {TARGET_METRIC}={val_target:.4f}, "
        f"gap={gap_pct:.1f}% (threshold {OVERFIT_GAP_THRESHOLD_PCT}%, source: {THRESHOLD_SOURCE}) -> overfit_flag={overfit}. "
        f"train beats baseline accuracy ({baseline_accuracy:.4f}) by {train_beats_pct:.1f}%, val by {val_beats_pct:.1f}% "
        f"(threshold {UNDERFIT_BASELINE_MARGIN_PCT}%) -> underfit_flag={underfit}. "
        f"CV fold {TARGET_METRIC} std/mean={fold_std_pct:.1f}% (threshold {FOLD_INSTABILITY_THRESHOLD_PCT}%) -> instability_flag={instability}. "
        f"{'PASSES' if passed else 'FAILS'} the generalization gate."
    )
    logger.info(reasoning)

    return GateResult(
        family=family,
        train_metrics=train_metrics,
        val_metrics=val_metrics,
        test_metrics=test_metrics,
        baseline_metrics=baseline,
        train_val_gap_pct=gap_pct,
        fold_std_pct=fold_std_pct,
        overfit_flag=overfit,
        underfit_flag=underfit,
        instability_flag=instability,
        passed=passed,
        reasoning=reasoning,
    )


def audit_feature_leakage(features: pd.DataFrame, labels: pd.Series, threshold: float = 0.95) -> dict[str, dict]:
    """Per-feature correlation with the label, logged for every feature individually --
    not a blanket claim. Every feature here is backward-looking by construction (see
    core.ml.feature_pipeline's no-lookahead regression test), but a suspiciously
    perfect correlation is still worth surfacing as a leakage risk requiring review."""
    results: dict[str, dict] = {}
    label_float = labels.astype(float)
    for col in features.columns:
        corr = features[col].corr(label_float)
        is_valid = pd.notna(corr)
        results[col] = {
            "correlation_with_label": float(corr) if is_valid else None,
            "leakage_risk": bool(is_valid and abs(corr) > threshold),
        }
    flagged = [c for c, r in results.items() if r["leakage_risk"]]
    if flagged:
        logger.warning("Feature leakage audit flagged: %s (|correlation| > %.2f)", flagged, threshold)
    else:
        logger.info("Feature leakage audit: 0 of %d features flagged (|correlation| > %.2f)", len(results), threshold)
    return results
