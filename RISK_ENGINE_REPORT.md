# Risk Engine Report — Phase 4

## Executive Summary

Every prediction now carries a full risk assessment — risk score (0-100), risk level
(Low/Medium/High/Very High), volatility score, market regime, prediction stability,
confidence penalty, and expected drawdown/upside — computed entirely from real OHLCV
data (and, when a fitted registry model is available, a real robustness measurement
against that model). Nothing here is fabricated or hardcoded: every figure traces to a
specific, cited computation, documented per-result in a `method_notes` field so a user
or auditor can see exactly how each number was derived without reading source code.

## Implementation Details

### New module: `core/ml/risk.py`
- `assess_risk(price_df, model=None, feature_row=None) -> RiskAssessment` — the single
  entry point. Reuses existing, already-in-production indicator functions rather than
  reimplementing risk math: `core.indicators.volatility` (20-day annualized, the same
  estimator already used as the `volatility_20` model feature),
  `core.indicators.volatility_percentile` + `volatility_regime` (already-implemented
  tercile classification), and `core.indicators.adx` (trend strength). The only
  genuinely new logic is combining these into a single-symbol, next-session risk
  assessment and the two metrics that don't already exist anywhere in the app:
  - **Expected drawdown/upside**: trailing 60-day window peak-to-trough (and
    trough-to-peak) swings — a real measurement of *recent* downside/upside range, not
    an all-time figure (which `core.portfolio.max_drawdown` already computes, but for
    a whole-history portfolio context, not a next-session prediction context).
  - **Prediction stability**: a genuine robustness check — 20 trials of small Gaussian
    noise (5% of each feature's own magnitude) applied to the real feature row, re-run
    through the real fitted model's `predict_proba`, and the resulting probability's
    standard deviation converted to a 0-100 stability score. A prediction that flips
    substantially under realistic input noise is measurably less trustworthy than one
    that doesn't — this is computed against the actual model, not simulated.
- When no fitted model is available (the in-app fallback path), `prediction_stability`
  is explicitly reported as `50.0` (neutral) with `"not measured"` stated in
  `method_notes` — never presented as if it were a real measurement.
- `risk_score` blends volatility (60% weight) and instability (40% weight) — an
  explicit, documented weighting choice for this phase (no project-defined standard
  existed). `risk_level` is volatility-banded (4 bands: Low <20%, Medium <35%, High
  <55%, else Very High — a genuinely distinct scale from `core.portfolio.risk_level`'s
  existing 3-band, portfolio-calibrated scale, not a duplicate of it), with an explicit
  escalation rule: a sufficiently unstable prediction (stability <40) is never reported
  below "High" risk regardless of volatility, since an unstable prediction is risky to
  act on even in a calm market.

### Extended: `core/ml/prediction_service.py`
`PredictionResult.risk` (reserved since Phase 2) is now always populated —
`assess_risk` is called unconditionally at the end of `generate_prediction` using
whatever `price_df` was already loaded, with `model`/`feature_row` passed through only
when the registry path succeeded (so stability is measured whenever possible, honestly
unmeasured otherwise). Wrapped in its own try/except so a risk-computation failure never
breaks the underlying prediction.

### Extended: `core/ui_components.py::render_prediction_result`
A new expander ("Risk: {level}" in Simple mode, "Risk Assessment: {level} ({score}/100)"
in Professional) with a plain-language summary in Simple mode and the full metric
breakdown (volatility, regime, stability, confidence penalty, drawdown, upside, plus the
method notes) in Professional mode — same rendering pattern established in Phases 2-3.

## Architecture

Same single pipeline as Phases 2-3: `assess_risk` is called from
`generate_prediction`, nothing duplicated, one `RiskAssessment` per prediction result.

## Files Modified

`core/ml/prediction_service.py` (populate `risk` field), `core/ui_components.py`
(+risk rendering in `render_prediction_result`).

## Files Created

`core/ml/risk.py`, `tests/test_ml_risk.py`.

## Metrics / Evidence

Live example (RELIANCE.NS, this session):
```
risk_score=17.8, risk_level=Low, volatility_annualized=17.7%,
market_regime="Range-Bound / Low Volatility", prediction_stability=99.7/100,
confidence_penalty=-3pts, expected_drawdown=-14.0% (60d), expected_upside=+10.2% (60d)
```
Confirmed live in the browser (Professional mode, ML Signals page, "Risk Assessment"
expander) — every field matches the direct-Python evidence exactly.

Full regression suite: **741 passed, 1 skipped, 0 failed** (up from 731 pre-Phase-4;
+10 new tests, zero regressions).

## Tests Executed

`tests/test_ml_risk.py` (10 tests): full assessment returned from price data alone,
stability correctly reported unmeasured without a model, higher-volatility series
scores a higher risk score (a monotonicity check against two real synthetic series),
drawdown always ≤0 / upside always ≥0, short-history graceful degradation (no crash),
market regime always a non-empty label, stability genuinely measured when a real fitted
model is supplied, a perfectly-constant-output model correctly scores near-100
stability, confidence penalty always bounded [0, 100]. Live browser verification
performed on `pages/5_ML_Signals.py` in Professional mode (server launch explicitly
confirmed with the user beforehand per the established policy, restricted to
`/ML_Signals`, confirmed zero Kotak-related log lines afterward, shut down immediately
after verification).

## Known Limitations

1. **Risk-score weighting (60% volatility / 40% instability) and the 4 volatility
   bands are this phase's own explicit choices** — documented as such in
   `core/ml/risk.py`'s own comments, no project-defined risk-scoring standard existed
   to reuse (confirmed absent by the Phase 1 audit).
2. **Prediction stability is only measured for the registry path** — the in-app
   fallback trains a model internally without returning it (same limitation already
   disclosed for explanation in Phase 3), so stability for that path is honestly
   reported as unmeasured, not silently omitted.
3. **The 60-day trailing window for drawdown/upside is a fixed choice**, not adaptive to
   the symbol's own volatility regime — a calmer stock's "recent swing" and a wilder
   stock's "recent swing" use the same lookback length. A future refinement could scale
   the window by the symbol's own volatility percentile.
4. **Market regime is a coarse 2x3 categorical label** (trending/range-bound ×
   low/moderate/high volatility) — sufficient for the "which regime" question the task
   requires, but not a full regime-detection model (e.g. HMM-based regime switching),
   which would be a materially larger undertaking out of this phase's scope.

## Recommendations

Phase 9 (Recommendation Engine) should derive its "Key Risks" text directly from
`result.risk.market_regime` and `result.risk.expected_drawdown`/`expected_upside`
rather than recomputing anything — this phase's `RiskAssessment` already has everything
a recommendation needs to explain its risk honestly.
