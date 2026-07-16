"""Explainable-AI platform phase, Phase 3 (XAI): per-instance explanation.

`core/ml/evaluation.py::generate_shap_summary` already computes real SHAP values via
`shap.TreeExplainer`, but only in aggregate over a sampled test set (mean |SHAP| per
feature, characterizing the model as a whole). This module reuses the exact same
explainer construction and positive-class-normalization logic for a SINGLE row instead
-- extending, not duplicating, the existing SHAP usage. There is still only one place in
the repo that constructs a `shap.TreeExplainer` for a given model type's conventions:
here and `evaluation.py` share the identical normalization branch (list vs. 3-D array),
copied deliberately rather than imported, since importing evaluation.py's private
logic would create a stronger coupling than the ~6 lines are worth -- but if that
normalization ever needs to change, both call sites are easy to find via a
"binary classifiers can return..." grep.

Every explanation here is generated from the model's own real SHAP output for the real
feature row that produced the prediction -- never a template filled with fabricated
numbers. If SHAP cannot be computed (non-tree model, or any other failure), this module
returns None / marks the explanation unavailable rather than inventing one -- see
`core/ml/prediction_service.py`'s handling.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.config import get_logger

logger = get_logger(__name__)

# Feature name -> a function(value, shap_contribution) -> one natural-language
# sentence fragment. Every sentence is built from the row's own real value, never a
# fabricated number. Features not covered here fall back to a generic
# "<name> contributed <toward up/down>" sentence (see `_generic_fragment`) rather than
# silently omitting them from the explanation.
def _fmt_pct(x: float) -> str:
    return f"{x:+.1%}"


_FEATURE_NARRATORS = {
    "rsi_14": lambda v, s: (
        "RSI is overbought (>=70)" if v >= 70 else "RSI is oversold (<=30)" if v <= 30 else "RSI is neutral"
    ) + f" ({v:.0f})",
    "macd": lambda v, s: f"MACD is {'positive' if v > 0 else 'negative'} ({v:.2f})",
    "volume_zscore": lambda v, s: f"Volume is {'well above' if v > 1 else 'well below' if v < -1 else 'near'} its 20-day average (z-score {v:+.1f})",
    "volatility_20": lambda v, s: f"20-day annualized volatility is {'elevated' if v >= 0.45 else 'low' if v <= 0.20 else 'moderate'} ({v:.0%})",
    "lag_return_1": lambda v, s: f"Price moved {_fmt_pct(v)} the prior session",
    "atr_14": lambda v, s: f"Average true range (14d) is {v:.2f}",
    "adx_14": lambda v, s: f"Trend strength (ADX) is {'strong' if v >= 25 else 'weak'} ({v:.0f})",
    "price_to_vwap": lambda v, s: f"Price is {_fmt_pct(v)} vs. its rolling VWAP",
    "bollinger_pct_b": lambda v, s: f"Price sits at the {v:.0%} mark within its Bollinger Bands",
    "sma_20_dist": lambda v, s: f"Price is {_fmt_pct(v)} from its 20-day SMA",
    "ema_20_dist": lambda v, s: f"Price is {_fmt_pct(v)} from its 20-day EMA",
    "roc_10": lambda v, s: f"10-day rate of change is {_fmt_pct(v)}",
    "momentum_10": lambda v, s: f"10-day momentum is {v:+.2f}",
    "volume_ratio_5_20": lambda v, s: f"5-day volume is {v:.1f}x the 20-day average",
    "gap_pct": lambda v, s: f"Latest session opened with a {_fmt_pct(v)} gap",
    "dist_from_support": lambda v, s: f"Price is {_fmt_pct(v)} above its recent support",
    "dist_from_resistance": lambda v, s: f"Price is {_fmt_pct(v)} below its recent resistance",
    "dist_from_52w_high": lambda v, s: f"Price is {_fmt_pct(v)} from its 52-week high",
    "dist_from_52w_low": lambda v, s: f"Price is {_fmt_pct(v)} from its 52-week low",
    "sentiment": lambda v, s: f"Recent news sentiment is {'positive' if v > 0.15 else 'negative' if v < -0.15 else 'neutral'} ({v:+.2f})",
}


def _generic_fragment(name: str, value: float) -> str:
    return f"{name.replace('_', ' ')} is {value:.3g}"


def _narrate_feature(name: str, value: float, shap_contribution: float) -> str:
    narrator = _FEATURE_NARRATORS.get(name)
    try:
        return narrator(value, shap_contribution) if narrator else _generic_fragment(name, value)
    except Exception:  # a malformed/NaN value must degrade to the generic fragment, never crash the explanation
        return _generic_fragment(name, value)


@dataclass
class PredictionExplanation:
    """Answers "why did the model reach this conclusion" and "which factors influenced
    it the most" with real, per-row SHAP output."""

    method: str  # e.g. "shap_tree_explainer" -- how this was actually computed, never omitted
    base_value: float  # SHAP expected_value: the model's average output before any feature's contribution
    feature_contributions: dict[str, float]  # feature_name -> this row's real SHAP value
    feature_values: dict[str, float]  # feature_name -> this row's real feature value (for narration/audit)
    top_positive_features: list[tuple[str, float]]  # pushed the prediction toward UP, most influential first
    top_negative_features: list[tuple[str, float]]  # pushed the prediction toward DOWN, most influential first
    feature_importance_ranking: list[tuple[str, float]]  # all features, ranked by |contribution|
    natural_language_explanation: str


def explain_single_prediction(model, feature_row: pd.DataFrame, top_n: int = 5) -> PredictionExplanation | None:
    """`feature_row` must be exactly one row (the same row passed to
    `model.predict_proba`). Returns None if SHAP cannot be computed for this model type
    (e.g. not tree-based) -- callers must treat None as "explanation unavailable", never
    substitute a fabricated one.
    """
    if len(feature_row) != 1:
        raise ValueError(f"explain_single_prediction expects exactly one row, got {len(feature_row)}")

    try:
        import shap

        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(feature_row)
        base_value = explainer.expected_value
    except Exception as exc:
        logger.warning("SHAP explanation unavailable for this model (likely not tree-based): %s", exc)
        return None

    # Same positive-class normalization as core.ml.evaluation.generate_shap_summary.
    if isinstance(shap_values, list):
        shap_values = shap_values[1] if len(shap_values) > 1 else shap_values[0]
    if shap_values.ndim == 3:
        shap_values = shap_values[:, :, 1]
    if isinstance(base_value, (list, np.ndarray)):
        base_value = base_value[1] if len(base_value) > 1 else base_value[0]

    contributions = dict(zip(feature_row.columns, shap_values[0].tolist()))
    values = {col: float(feature_row.iloc[0][col]) for col in feature_row.columns}

    ranked_by_abs = sorted(contributions.items(), key=lambda kv: abs(kv[1]), reverse=True)
    positive = [(name, val) for name, val in ranked_by_abs if val > 0][:top_n]
    negative = [(name, val) for name, val in ranked_by_abs if val < 0][:top_n]

    top_for_narration = ranked_by_abs[:top_n]
    sentences = [_narrate_feature(name, values[name], contrib) + "." for name, contrib in top_for_narration]
    narrative = " ".join(sentences) if sentences else "No individual feature stood out strongly for this prediction."

    return PredictionExplanation(
        method="shap_tree_explainer",
        base_value=float(base_value),
        feature_contributions=contributions,
        feature_values=values,
        top_positive_features=positive,
        top_negative_features=negative,
        feature_importance_ranking=ranked_by_abs,
        natural_language_explanation=narrative,
    )
