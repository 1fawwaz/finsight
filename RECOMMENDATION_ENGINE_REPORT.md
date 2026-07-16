# Recommendation Engine Report — Phase 9

## Executive Summary

Every prediction now carries a plain-language `Recommendation` — a directional lean
(never a "Buy"/"Sell" instruction), the model's honest prediction horizon, a
risk-derived reference level for how far the prediction could be wrong, key risks, and
an explicit "this is not financial advice" caveat. Nothing here is a new computation:
every field is synthesized purely from `PredictionResult` fields Phases 2-8 already
populated with real evidence (confidence, risk, drift, freshness, historical
performance) — this phase adds phrasing and synthesis, never a new data source or
model call, and never a claim the underlying evidence doesn't support.

## Implementation Details

### New module: `core/ml/recommendation.py`
- `Recommendation` dataclass: `stance` ("Leans Up"/"Leans Down" — deliberately not
  "Buy"/"Sell", consistent with this app's existing, established framing that
  predictions are research signals, not trading instructions —
  `core.explain.PREDICTION_DISCLAIMER` and the "research signal, not a recommendation
  to trade" caption already on ML Signals), `stance_strength` (the prediction's own
  `confidence_level`, reused verbatim, not a second scale), `horizon`, `reference_stop_level`,
  `reference_stop_note`, `key_risks`, `rationale`, `caveats`.
- `build_recommendation(result: PredictionResult) -> Recommendation | None` — the single
  synthesis function:
  - **Horizon**: this app's model predicts direction for exactly the next trading
    session (`core.market_status.prediction_target_session`) and has zero evidence
    about multi-day price behavior. The honest answer to "suggested holding period" is
    therefore that no multi-day period is suggested — stated explicitly, not silently
    omitted, so a user doesn't wonder why it's missing.
  - **Reference stop level ("stop loss if supported")**: derived directly from
    `result.risk.expected_drawdown` (for an "up" lean) or `expected_upside` (for a
    "down" lean) — Phase 4's real, measured trailing-60-day peak-to-trough/trough-to-peak
    swing for this specific symbol. Framed explicitly as a *reference point for how far
    the prediction could be wrong*, not a trade instruction, and reported as
    `None`/"Unavailable" (never a fabricated number) when `result.risk` itself is
    unavailable.
  - **Key risks**: assembled from `result.risk` (market regime, volatility, recent
    swings), `result.drift_status` (a "Significant Drift" flag becomes an explicit
    risk line), `result.data_freshness` (Stale/Unknown becomes an explicit risk line),
    and `result.confidence.confidence_level` (Low/Very Low becomes an explicit risk
    line) — every line traces to a field with real evidence already behind it.
  - **Rationale**: one to two sentences combining confidence and, when it exists, the
    real historical accuracy for this exact symbol/model pair
    (`result.historical_performance`) — explicitly states "no resolved historical track
    record exists yet" rather than fabricating a plausible-sounding number when `n=0`.
  - **Caveats**: always includes the "not financial advice" statement; adds a specific
    caveat when the prediction came from the unversioned in-app-fallback model rather
    than the registered, evaluated champion model.
- Returns `None` (not a fabricated recommendation) when `result.has_prediction` is
  `False` — there's nothing to summarize.

### Extended: `core/ml/prediction_service.py`
The `recommendation` field on `PredictionResult` was previously an untyped, unannotated
class attribute (`recommendation = None  # type: ignore[assignment]`) — a latent bug,
since a plain class attribute without a dataclass field annotation is shared across
instances rather than being a proper per-instance field. Fixed to
`recommendation: Optional[Recommendation] = None`, a correct dataclass field.
`build_recommendation(result)` is now called last inside `generate_prediction`, after
every other field is in its final state, so the recommendation reflects the complete
picture. This is cheap (pure synthesis of already-computed fields, no new data/model
calls) and safe to run eagerly on every prediction, unlike Phases 6-8's heavier lazy
lookups.

### Extended: `core/ui_components.py`
A new expander ("Leans Up (Medium)" in Simple mode, "Recommendation Summary: Leans Up
(Medium confidence)" in Professional), placed after the existing SHAP explanation
expander: rationale, horizon, the reference-stop note (Simple) or the reference level
plus a bulleted key-risks list (Professional), and every caveat — same `st.expander`
pattern established across Phases 3-4 and 6-8.

## Architecture

Same single pipeline as every prior phase: `build_recommendation` is called from
`generate_prediction`, reading only already-computed `PredictionResult` fields — no new
model, no new data source, no duplicate risk/confidence/drift logic.

## Files Modified

`core/ml/prediction_service.py` (fixed the `recommendation` field's type annotation,
+`build_recommendation` call), `core/ui_components.py` (+recommendation expander).

## Files Created

`core/ml/recommendation.py`, `tests/test_ml_recommendation.py`.

## Metrics / Evidence

Full regression suite: **811 passed, 1 skipped, 0 failed** (up from 796 pre-Phase-9; +15
new tests, zero regressions).

Real evidence gathered directly against live price history (RELIANCE.NS, this session):
```
stance=Leans Up, stance_strength=Very Low
horizon="This model predicts direction for the next trading session only -- it has no
  evidence about price behavior beyond that single session, so no multi-day holding
  period is suggested."
reference_stop_level=-0.1399 (derived from this symbol's real 60-day expected_drawdown)
reference_stop_note="This stock's own recent 60-day pullbacks have reached 14.0% -- a
  reference point for how far this 'up' prediction could be wrong by, not a specific
  trade instruction."
key_risks=[
  "Market regime: Range-Bound / Low Volatility.",
  "Recent volatility: 18% annualized (Low risk).",
  "Recent 60-day swings: as much as 14.0% down and 10.2% up.",
  "Model confidence is low -- this signal is close to a coin flip.",
]
rationale="The model leans up with Very Low confidence (50% probability of an up move).
  No resolved historical track record exists yet for this exact symbol/model
  combination."
caveats=["This is a research signal from a statistical model, not financial advice.",
  "Confidence and risk are as important as the direction itself -- never treat the
  direction alone as a certainty."]
```
Every number here matches the real `RiskAssessment`/`ConfidenceAssessment` already
verified in `RISK_ENGINE_REPORT.md`/`CONFIDENCE_ENGINE_REPORT.md` for this exact symbol
— nothing was recomputed or restated differently.

## Tests Executed

`tests/test_ml_recommendation.py` (14 tests): no prediction returns `None` (not a
fabricated recommendation), up/down predictions correctly lean up/down, the horizon
text never claims a multi-day holding period, a missing risk assessment correctly
leaves `reference_stop_level=None` with an "unavailable" note and a matching key-risk
line, an "up" lean correctly uses `expected_drawdown` as its reference level and a
"down" lean correctly uses `expected_upside`, significant drift / stale data / low
confidence each correctly add their own key-risk line, the rationale correctly reflects
real historical performance when present and explicitly states its absence when not,
the in-app-fallback path adds its own caveat, and the "not financial advice" caveat is
always present.

`tests/test_ml_prediction_service.py` (+1 test): `generate_prediction` always populates
`result.recommendation` with a real stance and never a fabricated multi-day horizon
claim.

## Known Limitations

1. **The reference-stop-level derivation (up-lean uses `expected_drawdown`, down-lean
   uses `expected_upside`) is this phase's own explicit design choice** — no
   project-defined stop-loss methodology existed to reuse (confirmed absent by the
   Phase 1 audit, same pattern as Phase 4's risk-weighting and Phase 8's drift
   thresholds: an original, documented choice, not an established standard). It answers
   "how far has this stock recently moved against a position in the predicted
   direction," which is a defensible reference point, but is explicitly framed as a
   reference, not a specific instruction, precisely because no backtested stop-loss
   strategy exists elsewhere in this codebase to point to instead.
2. **No live-browser screenshot was taken for this phase**, consistent with every phase
   since the accidental Kotak Neo live-auth incident earlier in this session.
   Correctness is evidenced by the direct-Python evidence above, 15 passing unit tests,
   and code review of the rendering logic.
3. **The recommendation's "key risks" list length and exact wording are fixed, not
   configurable** — every risk factor currently available (`result.risk`, drift,
   freshness, confidence) is always included when applicable; there's no mechanism yet
   to prioritize or truncate the list if more risk factors are added by future phases.
   Not a problem today (the list is short and every line is real), but worth revisiting
   if it grows substantially.

## Recommendations

1. Phase 10 (AI Dashboard) can surface `result.recommendation.stance`/`stance_strength`
   directly as one of its summary tiles — it's already a compact, real synthesis of
   everything else on the dashboard.
2. If a future phase adds a genuinely backtested stop-loss/exit strategy, this module's
   `reference_stop_level` should be replaced by that strategy's real output rather than
   the current volatility-derived reference — flagged here so that replacement doesn't
   get missed.
