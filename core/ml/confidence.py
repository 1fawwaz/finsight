"""Explainable-AI platform phase: confidence scoring and banding.

Confidence is derived entirely from the model's own (calibrated, where available)
probability output -- never a hardcoded constant. `pages/5_ML_Signals.py` previously
fell back to a hardcoded 0.55 "historical accuracy" when no backtest had been run this
session; this module has no such fallback, by design -- callers that lack a real
probability must not call `confidence_score_from_probability` at all, and must instead
mark confidence as unavailable (see core.ml.prediction_service).
"""

from __future__ import annotations

from dataclasses import dataclass

CONFIDENCE_LEVELS = ("Very High", "High", "Medium", "Low", "Very Low")

# Bucket thresholds on confidence_score (0-100, see confidence_score_from_probability).
# These are a documented, explicit threshold choice -- not a project-defined standard
# found anywhere else in the repo (none exists; confirmed by repo-wide search during the
# Phase 1 audit) -- chosen so a genuinely coin-flip-close probability (this project's
# actual champion model, ROC-AUC 0.515, produces calibrated probabilities within a few
# points of 50% almost always -- see CONFIDENCE_ENGINE_REPORT.md) correctly lands in
# "Very Low", rather than thresholds tuned to make a weak model look more confident than
# its own calibration says it is.
_THRESHOLD_SOURCE = "explicit choice for this phase -- no project-defined confidence-band standard exists elsewhere in the repo"
CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "Very High": 80.0,
    "High": 60.0,
    "Medium": 35.0,
    "Low": 15.0,
    # "Very Low" is anything below the Low threshold.
}


@dataclass(frozen=True)
class ConfidenceAssessment:
    """Everything Phase 2 requires to be exposed for one prediction."""

    probability_up: float  # the (possibly calibrated) probability the model assigns to "up"
    probability_down: float  # 1 - probability_up, exposed explicitly as "Probability Distribution"
    prediction_class: str  # "UP" or "DOWN" -- the class predict_proba favors
    confidence_score: float  # 0-100, distance of probability_up from an uninformative 50/50
    confidence_level: str  # one of CONFIDENCE_LEVELS
    was_calibrated: bool  # True if a fitted calibration temperature was applied
    threshold_source: str = _THRESHOLD_SOURCE


def confidence_score_from_probability(probability_up: float) -> float:
    """0 (probability_up == 0.5, an uninformative coin flip) to 100 (probability_up == 0
    or 1, maximal certainty). Symmetric around 0.5 since the model's probability of
    "down" carries exactly the complementary information."""
    return abs(probability_up - 0.5) * 2 * 100


def confidence_level_from_score(confidence_score: float) -> str:
    if confidence_score >= CONFIDENCE_THRESHOLDS["Very High"]:
        return "Very High"
    if confidence_score >= CONFIDENCE_THRESHOLDS["High"]:
        return "High"
    if confidence_score >= CONFIDENCE_THRESHOLDS["Medium"]:
        return "Medium"
    if confidence_score >= CONFIDENCE_THRESHOLDS["Low"]:
        return "Low"
    return "Very Low"


def assess_confidence(probability_up: float, was_calibrated: bool) -> ConfidenceAssessment:
    """The single entry point: model's probability_up -> a full ConfidenceAssessment.
    `probability_up` should already be calibrated when a calibration temperature exists
    for the serving model (see core.ml.registry.apply_calibration) -- this function does
    not calibrate anything itself, only scores/bands whatever probability it's given."""
    probability_up = max(0.0, min(1.0, float(probability_up)))
    score = confidence_score_from_probability(probability_up)
    return ConfidenceAssessment(
        probability_up=probability_up,
        probability_down=1.0 - probability_up,
        prediction_class="UP" if probability_up >= 0.5 else "DOWN",
        confidence_score=score,
        confidence_level=confidence_level_from_score(score),
        was_calibrated=was_calibrated,
    )
