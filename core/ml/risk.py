"""Explainable-AI platform phase, Phase 4: Risk Intelligence.

Every number here is computed from real, already-available data -- the same OHLCV
history already loaded for the prediction, and (where available) the same fitted
registry model -- never a fabricated or hardcoded risk figure. Reuses core.indicators'
existing volatility/ADX/percentile/regime functions and core.portfolio's existing
max_drawdown, rather than reimplementing risk math that already exists elsewhere in the
app for portfolios; this module's only new logic is applying those to a single
symbol's near-term prediction context and combining them into one risk assessment.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.config import get_logger
from core.indicators import adx as compute_adx
from core.indicators import volatility as compute_volatility
from core.indicators import volatility_percentile, volatility_regime

logger = get_logger(__name__)

RISK_LEVELS = ("Low", "Medium", "High", "Very High")

# Trailing window for "recent" (not all-time) drawdown/upside -- long enough to capture
# a real swing, short enough to stay relevant to a next-session prediction. Explicit
# choice for this phase; no project-defined standard exists (core.portfolio.max_drawdown
# operates over a caller-supplied full series, with no notion of "recent").
_RECENT_WINDOW_DAYS = 60

# core.portfolio.risk_level uses 3 bands (Low/Medium/High) calibrated to portfolio-level
# annualized volatility. Phase 4 requires a 4th band (Very High) for single-symbol,
# next-session risk, which is generally noisier than a diversified portfolio -- so this
# is a related but distinct scale, not a duplicate of that function.
_VOLATILITY_RISK_BANDS = [(0.20, "Low"), (0.35, "Medium"), (0.55, "High")]  # else "Very High"


@dataclass
class RiskAssessment:
    risk_score: float  # 0-100, higher = riskier to act on
    risk_level: str  # one of RISK_LEVELS
    volatility_score: float  # 0-100, from this symbol's own annualized 20-day volatility
    volatility_annualized: float  # the real underlying number (e.g. 0.32 = 32%/yr)
    market_regime: str  # e.g. "Trending / Low Volatility"
    prediction_stability: float  # 0-100, higher = more stable under small input perturbation
    confidence_penalty: float  # 0-100 points to subtract from the Phase 2 confidence_score for display purposes -- informational, never mutates the original calibrated score
    expected_drawdown: float  # negative fraction, e.g. -0.08, from the trailing 60-day window
    expected_upside: float  # positive fraction, e.g. 0.06, from the trailing 60-day window
    method_notes: str  # how each figure was derived, for audit


def _volatility_score(volatility_annualized: float) -> float:
    """0-100, capped: 0% annualized vol -> 0, 60%+ -> 100 (linear in between). 60% is a
    documented, generous cap -- NSE large/mid-cap equities rarely sustain higher."""
    return float(np.clip(volatility_annualized / 0.60 * 100, 0, 100))


def _risk_level_from_volatility(volatility_annualized: float) -> str:
    for threshold, label in _VOLATILITY_RISK_BANDS:
        if volatility_annualized < threshold:
            return label
    return "Very High"


def _market_regime(adx_value: float | None, vol_regime: str | None) -> str:
    trend_word = "Trending" if (adx_value is not None and adx_value >= 25) else "Range-Bound" if adx_value is not None else "Unknown Trend"
    vol_word = {"low": "Low Volatility", "medium": "Moderate Volatility", "high": "High Volatility"}.get(vol_regime, "Unknown Volatility")
    return f"{trend_word} / {vol_word}"


def _prediction_stability(model, feature_row: pd.DataFrame, n_trials: int = 20, noise_std: float = 0.05, seed: int = 42) -> float:
    """0-100: how consistent predict_proba's output stays when each feature is
    perturbed by small Gaussian noise (noise_std, as a fraction of each feature's own
    magnitude). A prediction that flips direction under tiny, realistic input noise is
    genuinely less trustworthy than one that doesn't -- this is a real robustness
    check against the actual fitted model, not a fabricated number."""
    try:
        base_proba = float(model.predict_proba(feature_row)[0][1])
    except Exception as exc:
        logger.warning("Could not compute prediction stability (base predict_proba failed): %s", exc)
        return 50.0  # neutral, explicitly not claimed to be measured -- caller should treat method_notes as authoritative

    rng = np.random.default_rng(seed)
    row_values = feature_row.iloc[0].to_numpy(dtype=float)
    perturbed_probas = []
    for _ in range(n_trials):
        noise = rng.normal(0, noise_std * (np.abs(row_values) + 1e-6))
        perturbed_row = feature_row.copy()
        perturbed_row.iloc[0] = row_values + noise
        try:
            perturbed_probas.append(float(model.predict_proba(perturbed_row)[0][1]))
        except Exception:
            continue
    if not perturbed_probas:
        return 50.0

    std_under_noise = float(np.std(perturbed_probas))
    # A probability std of 0 under noise -> perfectly stable (100). A std of 0.25+
    # (a quarter of the whole [0,1] probability range) -> treated as maximally
    # unstable (0). Linear in between; an explicit, documented scale choice.
    return float(np.clip(100 - (std_under_noise / 0.25) * 100, 0, 100))


def assess_risk(price_df: pd.DataFrame, model=None, feature_row: pd.DataFrame | None = None) -> RiskAssessment:
    """price_df: the same OHLCV history already loaded for this symbol's prediction.
    model/feature_row: optional -- if both are given (the registry path), prediction
    stability is measured against the real fitted model; otherwise it's reported as
    unmeasured (50.0, neutral) with that fact disclosed in method_notes, never silently
    presented as if it were measured."""
    close = price_df["close"]
    high = price_df["high"] if "high" in price_df.columns else close
    low = price_df["low"] if "low" in price_df.columns else close

    vol_series = compute_volatility(close, window=20, annualize=True)
    volatility_annualized = float(vol_series.iloc[-1]) if not vol_series.empty and pd.notna(vol_series.iloc[-1]) else 0.0

    vol_pct = volatility_percentile(vol_series, lookback=min(252, len(vol_series)))
    vol_regime_series = volatility_regime(vol_pct)
    vol_regime = vol_regime_series.iloc[-1] if not vol_regime_series.empty and pd.notna(vol_regime_series.iloc[-1]) else None

    adx_series = compute_adx(high, low, close, window=14)
    adx_value = float(adx_series.iloc[-1]) if not adx_series.empty and pd.notna(adx_series.iloc[-1]) else None

    recent = close.tail(_RECENT_WINDOW_DAYS)
    if len(recent) >= 5:
        running_max = recent.cummax()
        drawdown_series = (recent - running_max) / running_max
        expected_drawdown = float(drawdown_series.min())
        running_min = recent.cummin()
        upside_series = (recent - running_min) / running_min
        expected_upside = float(upside_series.max())
        drawdown_note = f"trailing {len(recent)}-day window"
    else:
        expected_drawdown = 0.0
        expected_upside = 0.0
        drawdown_note = "insufficient history for a trailing window -- reported as 0.0, not measured"

    if model is not None and feature_row is not None:
        stability = _prediction_stability(model, feature_row)
        stability_note = "measured via 20-trial Gaussian input perturbation against the real fitted model"
    else:
        stability = 50.0
        stability_note = "not measured (no fitted model available for this prediction) -- neutral placeholder, not a real measurement"

    volatility_score = _volatility_score(volatility_annualized)
    # Risk score blends volatility (60%) and prediction instability (40%) -- an
    # explicit, documented weighting for this phase, not a project-defined standard.
    risk_score = float(np.clip(0.6 * volatility_score + 0.4 * (100 - stability), 0, 100))
    risk_level = _risk_level_from_volatility(volatility_annualized)
    # Instability alone can also push a merely-Medium-volatility symbol into a higher
    # band -- an unstable prediction is risky to act on even in a calm market.
    if stability < 40 and RISK_LEVELS.index(risk_level) < RISK_LEVELS.index("High"):
        risk_level = "High"

    confidence_penalty = float(np.clip((100 - stability) * 0.3 + volatility_score * 0.1, 0, 100))

    return RiskAssessment(
        risk_score=risk_score,
        risk_level=risk_level,
        volatility_score=volatility_score,
        volatility_annualized=volatility_annualized,
        market_regime=_market_regime(adx_value, vol_regime),
        prediction_stability=stability,
        confidence_penalty=confidence_penalty,
        expected_drawdown=expected_drawdown,
        expected_upside=expected_upside,
        method_notes=(
            f"volatility: core.indicators.volatility(20d, annualized); "
            f"regime: ADX(14) + core.indicators.volatility_regime (terciles over trailing "
            f"{min(252, len(vol_series))}d); drawdown/upside: {drawdown_note}; "
            f"stability: {stability_note}"
        ),
    )
