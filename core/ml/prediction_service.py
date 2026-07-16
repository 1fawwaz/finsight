"""Explainable-AI platform phase: the single prediction orchestration point.

Per the project's own repository rules ("maintain one prediction pipeline"), this
module does not reimplement prediction, calibration, explanation, or risk logic --
it calls the existing `core.ml_model.predict_next_direction` (registry-first, in-app
fallback, unchanged) and existing `core.ml.*` modules, and assembles their outputs into
one `PredictionResult` that answers every question in the Engineering Constitution.

Every field on `PredictionResult` is either populated with real evidence or explicitly
marked unavailable (`None` + a reason in `warnings`) -- never fabricated. Fields not yet
wired up by a given implementation phase stay `None` by construction; later phases fill
them in without changing this dataclass's shape for already-shipped phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from core.config import get_logger
from core.ml.confidence import ConfidenceAssessment, assess_confidence
from core.ml.explanation import PredictionExplanation
from core.ml.performance import PerformanceStats, overall_performance
from core.ml.recommendation import Recommendation, build_recommendation
from core.ml.risk import RiskAssessment, assess_risk
from core.ml_model import REGISTRY_MODEL_NAME, build_features, make_dataset, predict_next_direction

logger = get_logger(__name__)


@dataclass
class PredictionResult:
    """Answers the Engineering Constitution's 10 questions for one prediction. See the
    module docstring for the "populate or mark unavailable, never fabricate" rule."""

    symbol: str
    generated_at: datetime

    # Q1: What is the prediction? / Q2: How confident is the model?
    confidence: Optional[ConfidenceAssessment] = None

    # Q7: Which model produced this prediction?
    model_source: Optional[str] = None  # "registry" or "in_app_fallback" or None if no prediction was possible
    model_name: Optional[str] = None
    model_version: Optional[str] = None
    model_status: Optional[str] = None

    # Q8: Which dataset was used?
    dataset_version: Optional[str] = None
    feature_version: Optional[str] = None
    dataset_size: Optional[int] = None  # row count of the dataset version, from MLDatasetVersion

    # Q9: How fresh is the underlying market data?
    data_freshness: Optional[str] = None  # "Fresh" / "Delayed" / "Stale" / "Unknown"
    latest_market_timestamp: Optional[datetime] = None

    # Q4: Which factors influenced it the most? / Q3: Why did the model reach this conclusion?
    explanation: Optional[PredictionExplanation] = None

    # Q5: How risky is acting on this prediction?
    risk: Optional[RiskAssessment] = None

    # Q6: How accurate has this model been historically?
    historical_performance: Optional[PerformanceStats] = None

    # Q10: When should this prediction no longer be trusted? (Phase 8, drift-derived)
    trust_until: Optional[str] = None
    drift_status: Optional[str] = None

    # Recommendation: a plain-language research summary synthesized from the fields
    # above -- never a fabricated buy/sell instruction (see core.ml.recommendation).
    recommendation: Optional[Recommendation] = None

    # Anything this result could NOT answer with evidence, and why -- surfaced to the UI
    # per the Engineering Constitution's "do not display the prediction without clearly
    # marking the missing information" rule.
    warnings: list[str] = field(default_factory=list)

    @property
    def has_prediction(self) -> bool:
        return self.confidence is not None


def generate_prediction(symbol: str, price_df: pd.DataFrame, sentiment_by_date: Optional[pd.Series] = None) -> PredictionResult:
    """The single entry point every page/dashboard should call for a symbol's next-
    session prediction. Wraps core.ml_model.predict_next_direction (unchanged) and
    layers calibration + confidence banding on top; later phases add explanation/risk/
    freshness/drift/recommendation without changing this function's basic shape.
    """
    result = PredictionResult(symbol=symbol, generated_at=datetime.now(timezone.utc))

    raw_result = predict_next_direction(price_df, sentiment_by_date)
    if raw_result is None:
        result.warnings.append("Not enough price history to produce a prediction for this symbol.")
        return result

    predicted_up, raw_probability_up = raw_result

    # Determine which path produced this prediction (registry vs. in-app fallback) and,
    # if registry, apply that model's own fitted calibration -- re-deriving this here
    # rather than changing predict_next_direction's return signature (a change that
    # would ripple into every existing caller, unnecessary for this phase).
    calibrated_probability_up = raw_probability_up
    was_calibrated = False
    model = None
    latest_features = None
    try:
        from core.ml.registry import apply_calibration, get_active_model

        model, entry = get_active_model(REGISTRY_MODEL_NAME)
        if model is not None:
            # A prediction actually came from the registry model only if its own
            # feature computation succeeds cleanly for this symbol -- recomputed here
            # deliberately (cheap, ~28ms) rather than threading a "which path" flag
            # through predict_next_direction, to avoid touching that function's
            # contract at all.
            from core.ml.feature_pipeline import build_features_v2

            candidate_features = build_features_v2(price_df, sentiment_by_date).iloc[[-1]]
            if hasattr(model, "feature_names_in_"):
                candidate_features = candidate_features.reindex(columns=model.feature_names_in_)
            registry_path_used = not candidate_features.isna().to_numpy().any()

            if registry_path_used:
                latest_features = candidate_features
                result.model_source = "registry"
                result.model_name = entry.model_name
                result.model_version = entry.version
                result.model_status = entry.status
                result.dataset_version = entry.dataset_version
                result.feature_version = entry.feature_version
                calibrated_probability_up, was_calibrated = apply_calibration(entry, raw_probability_up)
                if not was_calibrated:
                    result.warnings.append(
                        "This model version has not been calibrated yet (core.ml.registry.fit_and_store_calibration "
                        "has not been run for it) -- showing the model's raw, uncalibrated probability."
                    )

                try:
                    from core.ml.explanation import explain_single_prediction

                    result.explanation = explain_single_prediction(model, latest_features)
                    if result.explanation is None:
                        result.warnings.append(
                            "Per-prediction explanation is unavailable for this model (SHAP could not be computed)."
                        )
                except Exception as exc:
                    logger.warning("Per-prediction explanation failed for %s: %s", symbol, exc)
                    result.warnings.append("Could not generate a per-prediction explanation for this symbol.")
    except Exception as exc:  # calibration/registry lookup must never break a prediction
        logger.warning("Calibration lookup failed for %s, using raw probability: %s", symbol, exc)
        result.warnings.append("Could not verify this prediction's calibration status; showing the raw model probability.")

    if result.model_source is None:
        result.model_source = "in_app_fallback"
        result.model_name = "in_app_random_forest"
        result.model_version = None
        result.model_status = None
        result.warnings.append(
            "No registered model was usable for this symbol (missing/incompatible features) -- this prediction "
            "used a freshly-trained in-app fallback model with no version history, calibration, or "
            "per-prediction explanation."
        )

    result.confidence = assess_confidence(calibrated_probability_up, was_calibrated)

    # Q8/Q9: dataset lineage + data freshness -- always computable from price_df alone,
    # even on the in-app-fallback path (which has no dataset_version, but still has real
    # price data to judge freshness against).
    try:
        from core.ml.dataset_intelligence import assess_freshness, dataset_version_info

        latest_ts = price_df.index[-1]
        latest_date = latest_ts.date() if hasattr(latest_ts, "date") else latest_ts
        result.latest_market_timestamp = latest_ts.to_pydatetime() if hasattr(latest_ts, "to_pydatetime") else latest_ts

        freshness_label, days_behind = assess_freshness(latest_date)
        result.data_freshness = freshness_label
        if freshness_label == "Stale":
            result.warnings.append(
                f"Market data is Stale ({days_behind} trading day(s) behind the most recent expected session) -- "
                "treat this prediction with extra caution."
            )
        elif freshness_label == "Unknown":
            result.warnings.append("Could not determine market data freshness for this symbol.")

        if result.dataset_version is not None:
            ds_info = dataset_version_info(result.dataset_version)
            if ds_info is not None:
                result.dataset_size = ds_info["row_count"]

        from core.market_status import next_trading_day

        next_session = next_trading_day(latest_date)
        result.trust_until = f"Next trading session ({next_session:%d %b %Y}) -- refresh this prediction once new market data is available."
    except Exception as exc:  # freshness/dataset lookup must never break a prediction
        logger.warning("Dataset intelligence lookup failed for %s: %s", symbol, exc)
        result.warnings.append("Could not assess dataset lineage or market data freshness for this symbol.")

    # Q10 (drift half): cheap live-drift signals (DB queries only, no feature-set
    # reload) -- the heavier feature/data-distribution drift check
    # (core.ml.drift.assess_drift) is exposed separately, on-demand, for the UI's
    # Professional-mode expander, since it loads the full training feature set.
    try:
        from core.ml.drift import _rollup_status, compute_concept_drift, compute_prediction_drift
        from core.ml.registry import list_registry_entries

        if result.model_source == "registry":
            registered_accuracy = None
            match = next((e for e in list_registry_entries(result.model_name) if e["version"] == result.model_version), None)
            if match is not None:
                registered_accuracy = match["metrics"].get("accuracy")
            pred_status, _ = compute_prediction_drift(symbol, result.model_version)
            concept_status, _ = compute_concept_drift(symbol, result.model_version, registered_accuracy)
            result.drift_status = _rollup_status([pred_status, concept_status])
            if result.drift_status == "Significant Drift":
                result.warnings.append(
                    "Significant prediction/concept drift detected for this symbol -- this model's live behavior has "
                    "shifted meaningfully from its registered evaluation; treat this prediction with extra caution."
                )
        else:
            result.drift_status = "Insufficient Data"
    except Exception as exc:  # drift lookup must never break a prediction
        logger.warning("Drift assessment failed for %s: %s", symbol, exc)
        result.warnings.append("Could not assess prediction/concept drift for this symbol.")

    try:
        result.risk = assess_risk(price_df, model=model if latest_features is not None else None, feature_row=latest_features)
    except Exception as exc:  # risk assessment must never break a prediction
        logger.warning("Risk assessment failed for %s: %s", symbol, exc)
        result.warnings.append("Could not compute a risk assessment for this symbol.")

    try:
        # Read-only (no writes -- see core.ml.prediction_tracking for the separate,
        # explicit write path), so safe to call unconditionally here even for a
        # never-before-seen symbol (returns n=0/None stats, not an error).
        result.historical_performance = overall_performance(symbol=symbol, model_version=result.model_version)
        if result.historical_performance.n == 0:
            result.warnings.append(
                "No resolved historical predictions exist yet for this symbol/model -- historical accuracy is unavailable."
            )
    except Exception as exc:
        logger.warning("Historical performance lookup failed for %s: %s", symbol, exc)
        result.warnings.append("Could not look up historical performance for this symbol.")

    try:
        # Cheap synthesis of already-computed fields (no new data/model calls) -- safe
        # to run last, after every other field above is in its final state.
        result.recommendation = build_recommendation(result)
    except Exception as exc:  # recommendation synthesis must never break a prediction
        logger.warning("Recommendation synthesis failed for %s: %s", symbol, exc)
        result.warnings.append("Could not synthesize a recommendation summary for this symbol.")

    return result
