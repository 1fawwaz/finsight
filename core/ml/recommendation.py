"""Explainable-AI platform phase, Phase 9: recommendation engine.

Turns a `PredictionResult`'s already-computed confidence/risk/drift/freshness evidence
into a plain-language research summary -- never a fabricated buy/sell trading
instruction. This app's own disclaimers, already applied everywhere else
(`core.explain.PREDICTION_DISCLAIMER`, the "research signal, not a recommendation to
trade" caption on ML Signals), are explicit that nothing here is financial advice, and
this module deliberately keeps that framing: it reports a directional *lean* and a
*strength*, not an imperative "Buy"/"Sell" instruction, and every number it surfaces
(stop-reference level, key risks) traces to a `PredictionResult` field that already has
real evidence behind it -- this module adds no new computation, only synthesis of
already-computed fields into readable text.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Recommendation:
    stance: str  # "Leans Up" / "Leans Down"
    stance_strength: str  # the prediction's confidence_level, reused verbatim -- not a second scale
    horizon: str  # the model's real, honest prediction horizon -- never a fabricated multi-day claim
    reference_stop_level: float | None  # a signed fractional move (e.g. -0.14), derived from risk.expected_drawdown/upside
    reference_stop_note: str
    key_risks: list[str] = field(default_factory=list)
    rationale: str = ""
    caveats: list[str] = field(default_factory=list)


def build_recommendation(result) -> Recommendation | None:
    """`result` is a `core.ml.prediction_service.PredictionResult` (untyped here to
    avoid a circular import -- this module is imported by prediction_service, not the
    other way around). Returns `None` (never a fabricated recommendation) when there's
    no actual prediction to base one on."""
    if not result.has_prediction:
        return None

    confidence = result.confidence
    stance = "Leans Up" if confidence.prediction_class == "UP" else "Leans Down"

    # This app's model predicts direction for exactly one trading session ahead
    # (core.market_status.prediction_target_session) -- it has no evidence at all about
    # multi-day price behavior, so a "suggested holding period" beyond that single
    # session would be fabricated. This is the honest answer, not a placeholder.
    horizon = (
        "This model predicts direction for the next trading session only -- it has no "
        "evidence about price behavior beyond that single session, so no multi-day "
        "holding period is suggested."
    )

    key_risks: list[str] = []
    reference_stop_level: float | None = None
    reference_stop_note = "Unavailable -- no risk assessment was computed for this prediction."

    if result.risk is not None:
        risk = result.risk
        key_risks.append(f"Market regime: {risk.market_regime}.")
        key_risks.append(f"Recent volatility: {risk.volatility_annualized:.0%} annualized ({risk.risk_level} risk).")
        key_risks.append(
            f"Recent 60-day swings: as much as {abs(risk.expected_drawdown):.1%} down and {risk.expected_upside:.1%} up."
        )
        if confidence.prediction_class == "UP":
            reference_stop_level = risk.expected_drawdown  # already signed negative
            reference_stop_note = (
                f"This stock's own recent 60-day pullbacks have reached {abs(risk.expected_drawdown):.1%} -- a "
                "reference point for how far this 'up' prediction could be wrong by, not a specific trade instruction."
            )
        else:
            reference_stop_level = risk.expected_upside
            reference_stop_note = (
                f"This stock's own recent 60-day rallies have reached {risk.expected_upside:.1%} -- a reference "
                "point for how far this 'down' prediction could be wrong by, not a specific trade instruction."
            )
    else:
        key_risks.append("No risk assessment is available for this prediction.")

    if result.drift_status == "Significant Drift":
        key_risks.append("This model shows significant live drift -- treat this prediction with extra caution.")
    if result.data_freshness in ("Stale", "Unknown"):
        key_risks.append(f"Market data freshness is {result.data_freshness} -- this prediction may be based on outdated data.")
    if confidence.confidence_level in ("Low", "Very Low"):
        key_risks.append("Model confidence is low -- this signal is close to a coin flip.")

    rationale = f"The model {stance.lower()} with {confidence.confidence_level} confidence. "
    if result.historical_performance is not None and result.historical_performance.n > 0:
        rationale += (
            f"Historically, this exact model/symbol combination has been right "
            f"{result.historical_performance.accuracy:.0%} of the time ({result.historical_performance.n} resolved predictions)."
        )
    else:
        rationale += "No resolved historical track record exists yet for this exact symbol/model combination."

    caveats = [
        "This is a research signal from a statistical model, not financial advice.",
        "Confidence and risk are as important as the direction itself -- never treat the direction alone as a certainty.",
    ]
    if result.model_source == "in_app_fallback":
        caveats.append("This prediction used an unversioned in-app fallback model, not the registered/evaluated champion model.")

    return Recommendation(
        stance=stance,
        stance_strength=confidence.confidence_level,
        horizon=horizon,
        reference_stop_level=reference_stop_level,
        reference_stop_note=reference_stop_note,
        key_risks=key_risks,
        rationale=rationale,
        caveats=caveats,
    )
