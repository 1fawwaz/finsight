# XAI Report — Phase 3 (Explainable AI)

## Executive Summary

Every registry-model prediction now generates a real, per-instance SHAP explanation:
top positive/negative contributing features, a full feature-importance ranking, and a
natural-language explanation built from that row's actual feature values — never a
template filled with fabricated numbers. This closes the single largest gap identified
in the Phase 1 audit: `core/ml/evaluation.py::generate_shap_summary` already computed
real SHAP values, but only in aggregate over a sampled test set, characterizing the
model as a whole rather than any individual prediction. No function anywhere in the
repo previously explained one row.

## Implementation Details

### New module: `core/ml/explanation.py`
- `explain_single_prediction(model, feature_row, top_n=5) -> PredictionExplanation |
  None` — the single new function. Takes the exact one-row feature DataFrame already
  used for `model.predict_proba`, runs `shap.TreeExplainer(model).shap_values(...)` on
  it, and applies the identical positive-class normalization logic already used in
  `core/ml/evaluation.py::generate_shap_summary` (list-vs-3D-array handling for
  different SHAP/model-library version combinations) — copied deliberately rather than
  imported (a private ~6-line branch not worth a cross-module coupling), noted in the
  module docstring so a future reader can find both call sites via one grep if that
  logic ever needs to change.
- Returns `None` (not a fabricated explanation) when SHAP can't be computed for a given
  model — verified by test against a `LogisticRegression` model, which correctly
  produces no explanation rather than a wrong one.
- A small, honest feature-narrator table (`_FEATURE_NARRATORS`) maps ~19 of the
  registered model's real 27 features to natural-language fragments built from that
  row's actual value (e.g. `rsi_14` → "RSI is neutral (52)"). Any feature not in the
  table (including any future feature added to the pipeline) falls back to a generic,
  still-real-value fragment (`"feature_name is 0.0123"`) rather than being silently
  dropped from the explanation or crashing on an unrecognized name.

### Extended: `core/ml/prediction_service.py`
`PredictionResult.explanation` (already reserved in Phase 2's dataclass shape) is now
populated: when the registry path is used, the same `latest_features` row already
computed for prediction is reused (no redundant recomputation) and passed to
`explain_single_prediction`. Any failure (non-tree model, SHAP exception) is caught and
surfaced as an explicit `result.warnings` entry — never silently blank, per the
Engineering Constitution's "clearly mark missing information" rule. The in-app fallback
path's warning was extended to explicitly name explanation as one of the things it
doesn't have (alongside version/calibration, already true from Phase 2), since
`predict_next_direction`'s fallback trains a fresh model internally without returning
it, so this phase has no fitted object to explain against.

### Extended: `core/ui_components.py::render_prediction_result`
Adds an expander ("Why?" in Simple mode, "Explanation (SHAP)" in Professional) showing
the natural-language narrative always, plus (Professional mode) the top-positive and
top-negative feature lists with their real contribution values and the explainer
method/base-value, so a technical user can audit exactly what drove the number.

## Architecture

No new pipeline. `explain_single_prediction` slots into the existing
`generate_prediction` orchestration point from Phase 2 — one prediction pipeline, one
explanation function, called from one place.

## Files Modified

`core/ml/prediction_service.py` (populate `explanation` field), `core/ui_components.py`
(+explanation rendering in the existing `render_prediction_result`).

## Files Created

`core/ml/explanation.py`, `tests/test_ml_explanation.py`.

## Metrics / Evidence

Live example (RELIANCE.NS, this session, registered XGBoost model):
```
Top positive: dist_from_52w_low (+0.0139), gap_pct (+0.0077), macd (+0.0067),
              dist_from_resistance (+0.0057), atr_14 (+0.0041)
Top negative: volume_zscore (-0.0051), lower_wick_pct (-0.0028), lag_return_1 (-0.0022),
              adx_14 (-0.0012), price_to_vwap (-0.0011)
Base value:   0.0487
Narrative:    "Price is +4.0% from its 52-week low. Latest session opened with a +0.0%
              gap. MACD is negative (-6.45). Price is -3.1% below its recent
              resistance. Volume is well below its 20-day average (z-score -1.5)."
```
Confirmed live in the browser (Professional mode, ML Signals page, "Explanation (SHAP)"
expander) matching this exact output.

Full regression suite: **731 passed, 1 skipped, 0 failed** (up from 722 pre-Phase-3;
+9 new tests, zero regressions).

## Tests Executed

`tests/test_ml_explanation.py` (9 tests): exactly-one-row contract enforcement, real
explanation returned for a fitted tree model, feature values in the explanation match
the real input row exactly, importance ranking sorted by absolute contribution
descending, positive/negative feature lists correctly signed, natural-language
explanation is non-empty and traceable to a real dominant feature, non-tree model
(`LogisticRegression`) correctly returns `None` rather than a fabricated explanation,
an unrecognized feature name degrades to a generic (still real-valued) fragment instead
of crashing. Live browser verification performed on `pages/5_ML_Signals.py` in
Professional mode (server launch explicitly confirmed with the user beforehand,
restricted to navigating directly to `/ML_Signals`, confirmed zero Kotak-related log
lines afterward, shut down immediately after verification).

## Known Limitations

1. **Only tree-based models get an explanation** — by design (`shap.TreeExplainer`
   requires a tree-based model; the task's own instructions say "for tree-based models,
   prefer SHAP," and the one registered model is XGBoost). If a linear model is ever
   registered under this pipeline, `explain_single_prediction` will correctly return
   `None`, and a coefficient-based explainer would need to be added as a second,
   explicitly model-family-gated path (not built in this phase, since no linear model
   is currently registered — would be premature to build for a case that doesn't exist).
2. **The in-app fallback path never has an explanation** — a real, disclosed gap (see
   Implementation Details above), not a missed case, since fixing it would require
   changing `core.ml_model.predict_next_direction`'s return contract, out of this
   phase's "don't touch the existing prediction function's signature" scope.
3. **The feature-narrator table covers 19 of 27 features explicitly** — the remaining 8
   (mostly less commonly discussed candle-anatomy/percentile features) use the generic
   fallback fragment, which is honest but less readable. Extending the table is
   low-risk, additive work if a future session wants more natural-sounding coverage.

## Recommendations

Phase 4 (Risk Intelligence) can reuse `result.explanation.feature_values` directly
(e.g. `volatility_20`, `atr_14` are already extracted per-row) rather than
recomputing them for risk scoring.
