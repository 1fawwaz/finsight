"""Phase 3 Step 2.5: Evaluation artifacts -- confusion matrix, learning curves, feature
importance, and SHAP values, generated from real model runs and persisted (JSON data +
PNG plots) rather than only described.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this runs in a pipeline/CI context, never a GUI
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix

from core.config import BASE_DIR, get_logger

logger = get_logger(__name__)

EVALUATION_DIR = BASE_DIR / "data" / "ml_evaluation"
EVALUATION_DIR.mkdir(parents=True, exist_ok=True)


def generate_confusion_matrix(model, X: pd.DataFrame, y: pd.Series, out_dir: Path) -> dict:
    preds = model.predict(X)
    cm = confusion_matrix(y, preds)
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(cm, cmap="Blues")
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center")
    ax.set_xticks([0, 1], ["Pred Down", "Pred Up"])
    ax.set_yticks([0, 1], ["Actual Down", "Actual Up"])
    ax.set_title("Confusion Matrix (test)")
    fig.tight_layout()
    png_path = out_dir / "confusion_matrix.png"
    fig.savefig(png_path)
    plt.close(fig)
    return {"matrix": cm.tolist(), "png_path": str(png_path)}


def generate_learning_curve(fold_metrics: list[dict], metric_name: str, out_dir: Path) -> dict:
    """A real learning curve from the walk-forward CV folds already run for this model:
    training-window size (which grows fold over fold) on the x-axis, validation metric
    on the y-axis -- shows how performance actually evolved with more training data,
    not a synthetic re-run."""
    folds = [f["fold"] for f in fold_metrics]
    values = [f[metric_name] for f in fold_metrics]
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.plot(folds, values, marker="o")
    ax.set_xlabel("Walk-forward fold (later = more training data)")
    ax.set_ylabel(metric_name)
    ax.set_title(f"Learning curve ({metric_name} per fold)")
    ax.axhline(0.5, color="gray", linestyle="--", linewidth=1, label="no-skill (0.5)")
    ax.legend()
    fig.tight_layout()
    png_path = out_dir / "learning_curve.png"
    fig.savefig(png_path)
    plt.close(fig)
    return {"folds": folds, "values": values, "png_path": str(png_path)}


def generate_feature_importance(model, feature_names: list[str], out_dir: Path, top_n: int = 15) -> dict:
    if not hasattr(model, "feature_importances_"):
        logger.warning("Model has no feature_importances_ attribute -- skipping.")
        return {"importances": {}, "png_path": None}
    importances = dict(zip(feature_names, model.feature_importances_.tolist()))
    ranked = dict(sorted(importances.items(), key=lambda kv: kv[1], reverse=True)[:top_n])

    fig, ax = plt.subplots(figsize=(6, 5))
    ax.barh(list(ranked.keys())[::-1], list(ranked.values())[::-1])
    ax.set_xlabel("Importance")
    ax.set_title(f"Top {top_n} Feature Importances")
    fig.tight_layout()
    png_path = out_dir / "feature_importance.png"
    fig.savefig(png_path)
    plt.close(fig)
    return {"importances": importances, "png_path": str(png_path)}


def generate_shap_summary(model, X: pd.DataFrame, out_dir: Path, sample_size: int = 500) -> dict:
    """SHAP values via TreeExplainer (supports RandomForest, XGBoost, LightGBM,
    CatBoost). Sampled for speed on a large test set -- SHAP's exact computation is
    per-row and this is meant to characterize the model, not audit every row."""
    import shap

    sample = X.sample(n=min(sample_size, len(X)), random_state=42)
    explainer = shap.TreeExplainer(model)
    shap_values = explainer.shap_values(sample)
    # Binary classifiers can return a single array or a list of per-class arrays,
    # depending on the library -- normalize to "the positive class" contribution.
    if isinstance(shap_values, list):
        shap_values = shap_values[1] if len(shap_values) > 1 else shap_values[0]
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, 1]

    mean_abs_shap = dict(zip(sample.columns, np.abs(shap_values).mean(axis=0).tolist()))
    ranked = dict(sorted(mean_abs_shap.items(), key=lambda kv: kv[1], reverse=True))

    fig, ax = plt.subplots(figsize=(6, 5))
    top = dict(list(ranked.items())[:15])
    ax.barh(list(top.keys())[::-1], list(top.values())[::-1])
    ax.set_xlabel("Mean |SHAP value|")
    ax.set_title("SHAP Feature Impact (sampled test rows)")
    fig.tight_layout()
    png_path = out_dir / "shap_summary.png"
    fig.savefig(png_path)
    plt.close(fig)

    return {"mean_abs_shap": ranked, "sample_size": len(sample), "png_path": str(png_path)}


def generate_full_evaluation(
    model,
    model_version: str,
    X_test: pd.DataFrame,
    y_test: pd.Series,
    fold_metrics: list[dict],
    target_metric: str,
    leakage_audit: dict,
) -> dict:
    """Run and persist the full required evaluation suite for one model version."""
    out_dir = EVALUATION_DIR / model_version
    out_dir.mkdir(parents=True, exist_ok=True)

    confusion = generate_confusion_matrix(model, X_test, y_test, out_dir)
    learning_curve = generate_learning_curve(fold_metrics, target_metric, out_dir)
    feature_importance = generate_feature_importance(model, list(X_test.columns), out_dir)
    shap_summary = generate_shap_summary(model, X_test, out_dir)

    flagged_leakage_features = [name for name, info in leakage_audit.items() if info["leakage_risk"]]
    top_shap_features = list(shap_summary["mean_abs_shap"].keys())[:10]
    leaky_in_top_shap = [f for f in top_shap_features if f in flagged_leakage_features]

    result = {
        "model_version": model_version,
        "confusion_matrix": confusion,
        "learning_curve": learning_curve,
        "feature_importance": feature_importance,
        "shap_summary": shap_summary,
        "leakage_features_flagged": flagged_leakage_features,
        "leaky_features_in_top_shap": leaky_in_top_shap,
    }

    with open(out_dir / "evaluation_summary.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    logger.info("Full evaluation persisted to %s", out_dir)
    return result
