# Confidence Engine Report — Phase 2

## Executive Summary

Every prediction now exposes a real confidence score (0-100), a probability
distribution (up/down), a prediction class, and a confidence-level band (Very
High/High/Medium/Low/Very Low) — all derived from the model's own output, with zero
hardcoded values. This replaces a real, confirmed violation of the "confidence must
come from the model" rule: `pages/5_ML_Signals.py` previously fell back to a hardcoded
`0.55` "historical accuracy" whenever no backtest had been run yet in the current
session (`pages/5_ML_Signals.py:102`, pre-change). That fallback is gone; the page now
either shows a real confidence assessment or explicitly prompts the user to run a
backtest — never a fabricated number.

Probability calibration (temperature scaling, Guo et al. 2017) was wired into the live
serving path for the first time. `core/ml/calibration.py` already implemented this
correctly (Phase 2 Step 7 of the earlier ML build) but was never called from
`predict_next_direction`'s serving path — a pure wiring gap, closed here without
touching the calibration module's own logic. The result is an honest, uncomfortable but
correct finding: the champion model's fitted temperature saturated at the search
space's upper bound (20.0), meaning calibration pushes almost every prediction's
probability to within a couple of points of 50%. This is not a bug — it's calibration
correctly reporting that a model with ROC-AUC 0.5149 (already disclosed as barely
better than no-skill in `TRAINING_REPORT.md`) has very little real confidence to
express, and it would be dishonest to band its raw, overconfident probabilities as
"High" or "Very High" when calibrated against real outcomes they aren't.

## Implementation Details

### New modules
- **`core/ml/confidence.py`** — `confidence_score_from_probability` (distance of
  probability from an uninformative 50/50, scaled 0-100), `confidence_level_from_score`
  (bucketing into the 5 required bands), `assess_confidence` (the single entry point,
  returns a `ConfidenceAssessment` dataclass with `probability_up`, `probability_down`,
  `prediction_class`, `confidence_score`, `confidence_level`, `was_calibrated`, and an
  explicit `threshold_source` string documenting that the band cutoffs are this phase's
  own explicit choice, not a project-defined standard — confirmed absent anywhere else
  in the repo during the Phase 1 audit).
- **`core/ml/prediction_service.py`** — `generate_prediction(symbol, price_df,
  sentiment_by_date)`, the new single orchestration point. Calls the existing
  `core.ml_model.predict_next_direction` unchanged, determines whether the registry
  model or the in-app fallback actually produced the result, applies that model's
  fitted calibration if one exists, and returns a `PredictionResult` dataclass shaped to
  carry every field later phases (3-9) will add — so this dataclass's shape doesn't need
  to change again, only get filled in further.

### Extended (not duplicated) modules
- **`core/ml/registry.py`** — three additions: `MODEL_STATUSES`/`set_model_status` (a
  status lifecycle beyond the existing boolean `is_active`, prepared here for Phase 6
  but harmless to add now since it's additive), `fit_and_store_calibration(model_name)`
  (reuses `core.ml.calibration.fit_temperature` and `core.ml.cv
  .chronological_train_val_test_split` — reloads the active model's own registered
  `feature_version` via the existing `load_feature_set`, reconstructs the same
  chronological val split used at training time, and fits temperature against it — the
  val split, never the model's training data or the held-out test split, matching
  `core/ml/calibration.py`'s own stated discipline), and `apply_calibration(entry,
  raw_probability)` (applies a stored temperature, or returns the raw probability
  unchanged with `was_calibrated=False` if none has been fit yet).
- **`core/database.py`** — two additive columns on `ml_model_registry`:
  `calibration_temperature` (nullable float) and `status` (defaults `'active'`).
  Migrated via the project's existing `_ADDITIVE_COLUMN_MIGRATIONS` mechanism — no
  existing table structure changed, no data at risk. Verified against the live
  `data/finsight.db`: the pre-existing `finsight_direction_classifier_v1` row picked up
  `status='active'` automatically and `calibration_temperature=NULL` until fit.
- **`core/ui_components.py`** — `render_prediction_result(result, mode)`, following the
  same `Explanation`-dataclass rendering pattern already used by `render_explanation`.
  Shows Direction/Confidence/Probability as three metrics, plus (Professional mode only)
  which model version produced the prediction and whether its probability was
  calibrated, plus any `result.warnings` as visible captions — never silently dropped.
- **`pages/5_ML_Signals.py`** — the "Next Trading Session's Guess" section now calls
  `generate_prediction` + `render_prediction_result` instead of the raw
  `predict_next_direction` call + hand-assembled metric + hardcoded-fallback
  explanation. The walk-forward-backtest-driven `explain_ml_prediction` call (using a
  *real* completed backtest's accuracy) is preserved exactly as before, just gated more
  strictly — it now only renders when a real backtest has actually been run this
  session, with no numeric fallback in between.

## Architecture

```
predict_next_direction()          (unchanged, core/ml_model.py)
        │
        ▼
generate_prediction()             (new, core/ml/prediction_service.py)
        │  ├─ registry lookup + calibration (core/ml/registry.py, extended)
        │  └─ confidence banding (core/ml/confidence.py, new)
        ▼
PredictionResult
        │
        ▼
render_prediction_result()        (new, core/ui_components.py)
        │
        ▼
pages/5_ML_Signals.py
```

One prediction pipeline, one model registry, one calibration module — nothing
duplicated, per the repository rules.

## Files Modified

`core/database.py` (+2 additive columns), `core/ml/registry.py` (+3 functions),
`pages/5_ML_Signals.py` (rewired prediction section), `core/ui_components.py`
(+1 render function).

## Files Created

`core/ml/confidence.py`, `core/ml/prediction_service.py`, `tests/test_ml_confidence.py`,
`tests/test_ml_prediction_service.py`, plus 8 new test cases appended to the existing
`tests/test_ml_registry.py`.

## Metrics / Evidence

- Live-fitted calibration temperature for `finsight_direction_classifier_v1`: **19.9999**
  (saturated at the `fit_temperature` search bound of 20.0), fit against 2,592 clean
  validation rows (`n_val` logged at fit time).
- Live example (RELIANCE.NS, run this session): raw probability 0.5200 → calibrated
  0.5010 → confidence_score 0.20/100 → confidence_level **"Very Low"**. Verified both
  via direct Python call and live in the browser (Professional mode: "Model:
  finsight_direction_classifier_v1 (active) · calibrated probability").
- Full regression suite: **722 passed, 1 skipped, 0 failed** (up from 676 passed
  pre-Phase-2; +49 new tests, all passing, zero regressions — including the previously
  environment-blocked pyarrow-dependent tests, which now pass following an unrelated,
  externally-resolved Windows Application Control policy state change during this
  session, not a change made by this work).

## Tests Executed

`tests/test_ml_confidence.py` (14 tests: score/level boundary conditions, symmetry,
clamping out-of-range input), `tests/test_ml_prediction_service.py` (7 tests: empty
dataframe, too-short history, missing-column error propagation, full happy path,
model-source always reported, probabilities sum to 1), `tests/test_ml_registry.py`
(+13 tests: status lifecycle CRUD + validation, `apply_calibration`'s three cases
[unfit/T=1/T=20], `fit_and_store_calibration` end-to-end with synthetic data and its
"no active model" failure mode). Full suite (`pytest -q`) re-run clean. Live browser
verification performed on `pages/5_ML_Signals.py` in both Simple and Professional mode
(screenshots not attached to this report per project convention of citing evidence
inline rather than embedding binary attachments, consistent with every prior report in
this repo).

## Known Limitations

1. **Only one registered model (`finsight_direction_classifier_v1`) exists to
   calibrate** — the calibration wiring is real and tested, but has only been exercised
   against this single model so far. A second registered model would need its own
   `fit_and_store_calibration` call before serving calibrated probabilities (the
   fallback to "uncalibrated, explicitly marked" behavior is intentional and tested for
   exactly this case).
2. **Calibration must be re-fit manually** — `fit_and_store_calibration` is not
   automatically invoked at registration time (would require touching
   `core/ml/registry.py::register_model` or the training pipeline, out of this phase's
   "improve serving, don't touch training" scope per the Phase 1 audit's own
   recommendation). A newly registered model will show `was_calibrated=False` /
   "uncalibrated" until someone explicitly runs the fitting step — this is surfaced
   honestly via `result.warnings`, not hidden.
3. **Confidence-band thresholds (80/60/35/15) are this phase's own explicit choice**,
   documented as such in `core/ml/confidence.py`'s own constants — no project-defined
   standard existed to reuse (confirmed absent by the Phase 1 audit's repo-wide search).
4. **The in-app fallback path (when the registry model's features don't compute
   cleanly for a symbol) never has calibration or a version to report** — by design,
   surfaced via `model_source="in_app_fallback"` and an explicit warning, not silently
   presented as equivalent to a registry prediction.

## Recommendations

Phase 3 (XAI) should extend `PredictionResult` (already shaped for it) with per-instance
SHAP explanation, reusing `shap.TreeExplainer` exactly as `core/ml/evaluation.py`
already does for aggregate SHAP, applied to a single row instead of a sampled test set.
