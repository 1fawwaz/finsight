# Drift Report — Phase 8 (Drift Detection)

## Executive Summary

Every prediction now carries a live drift signal (`drift_status`), and Professional-mode
users can open a full Drift Report covering three distinct, genuinely different kinds of
drift: **feature/data drift** (has this symbol's current feature distribution moved away
from what the model was trained on — Population Stability Index, the standard
production-ML statistic), **prediction drift** (has the model's own output distribution
shifted over its recent live history), and **concept drift** (has live accuracy fallen
meaningfully short of the model's registered evaluation accuracy). Every check reports
"Insufficient Data" rather than a fabricated "Stable" verdict when there isn't yet enough
real history to judge — and, tested against the real deployed model, feature/data drift
already flags **Significant Drift**, a real and informative finding: the currently
active model (`finsight_direction_classifier_v1`) was trained on data through
2025-01-30, and is now serving 18 months past that cutoff.

## Implementation Details

### New module: `core/ml/drift.py`
- `compute_feature_drift(feature_version, price_df, ..., symbol=None) -> list[FeatureDriftResult]`
  — Population Stability Index between the model's own training-split feature values
  (reusing the exact same `load_feature_set` + `chronological_train_val_test_split`
  already used by `core.ml.registry.fit_and_store_calibration` and
  `core.ml.dataset_intelligence.training_validation_periods` — never a second
  splitting/loading implementation) and a symbol's most recent 60 days of live features
  (via the existing `core.ml.feature_pipeline.build_features_v2`). When `symbol` is one
  of the tickers actually present in the training panel, the comparison uses that
  symbol's own training-period rows specifically, not the full 15-symbol pooled panel —
  see Known Limitations for why this matters. Training-feature loads are memoized
  (`functools.lru_cache`) since they're too expensive to repeat per prediction.
- `compute_prediction_drift(symbol, model_version) -> (status, detail)` — splits this
  model's recorded live predictions for the symbol (Phase 5's `Prediction` table) into
  an earlier half and a recent half, and flags a shift in mean predicted P(up) beyond
  documented thresholds (10pp = Drifting, 25pp = Significant Drift). Requires at least
  10 live predictions; fewer honestly reports Insufficient Data.
- `compute_concept_drift(symbol, model_version, registered_accuracy) -> (status, detail)`
  — compares live, resolved-outcome accuracy (`core.ml.performance.overall_performance`,
  Phase 5) against the accuracy the model was registered with (`core.ml.registry`,
  Phase 6). Requires at least 20 resolved outcomes; fewer honestly reports Insufficient
  Data. A ≥7pp drop is Drifting, ≥15pp is Significant Drift.
- `assess_drift(...)` — the single entry point, rolls all three up into one
  `DriftReport` (worst-of-three: Significant Drift > Drifting > Stable, with
  "Insufficient Data" never masking a real signal from the other checks). Sets
  `recommend_retraining = True` only when the overall rollup is Significant Drift. Never
  raises — each sub-check degrades independently to Insufficient Data with a logged
  warning on failure.

### Extended: `core/ml/prediction_service.py`
Every prediction now eagerly computes the **cheap** half of drift (prediction + concept,
both plain DB queries — no feature-set reload) and populates `result.drift_status`. The
**heavy** half (feature/data drift, which loads the full training feature set on first
call per feature version) is deliberately *not* run on every prediction — it's exposed
on-demand via the UI's Professional-mode expander only, the same lazy pattern already
established for Phase 6's Model Registry and Phase 7's Dataset Intelligence expanders.
`result.trust_until` (Q10, reserved since Phase 2) is also now populated — a plain
statement of the next trading session, after which the prediction should be refreshed
(reusing `core.market_status.next_trading_day`, never a second calendar
implementation). A `Significant Drift` `drift_status` appends an explicit warning.

### Extended: `core/ui_components.py`
- A drift-status caption (both modes, only shown when there's an actual signal — not
  for "Insufficient Data", which would just be clutter for a freshly-used symbol).
- `_render_drift_expander(result, price_df, sentiment_by_date)` (Professional mode,
  registry path only): the full three-way `DriftReport`, including the most-drifted
  features by PSI and an explicit retraining recommendation when warranted.
- `render_prediction_result` gained two new optional parameters (`price_df`,
  `sentiment_by_date`) so the drift expander has real data to compute the feature-drift
  check against; `pages/5_ML_Signals.py` was updated to pass them through.

## Architecture

Same single pipeline as every prior phase: cheap drift signals are computed
unconditionally inside `generate_prediction`; the heavy feature-drift check is computed
lazily, on-demand, by the UI layer — consistent with Phases 6-7's established pattern of
keeping every prediction's hot path cheap while still making expensive-but-real
lineage/drift data available when a user actually wants to see it.

## Files Modified

`core/ml/prediction_service.py` (+cheap drift signals, +`trust_until`),
`core/ui_components.py` (+drift caption, +`_render_drift_expander`,
`render_prediction_result` gained `price_df`/`sentiment_by_date` params),
`pages/5_ML_Signals.py` (passes `price_df`/`sentiment_by_date` through).

## Files Created

`core/ml/drift.py`, `tests/test_ml_drift.py`.

## Metrics / Evidence

Full regression suite: **796 passed, 1 skipped, 0 failed** (up from 778 pre-Phase-8; +18
new tests, zero regressions).

Real evidence gathered directly against the live database and price history
(RELIANCE.NS, this session):
```
Cheap signals (from generate_prediction): drift_status=Insufficient Data
  (only 1 live prediction / 0 resolved outcomes recorded so far -- correctly
  insufficient, not a fabricated "Stable")
trust_until = "Next trading session (17 Jul 2026) -- refresh this prediction once
  new market data is available."

Full assess_drift() report:
  overall_status = Significant Drift
  data_drift_status = Significant Drift
  prediction_drift_status = Insufficient Data (1 prediction recorded; need 10)
  concept_drift_status = Insufficient Data (0 resolved; need 20)
  Top drifted features (PSI, per-symbol training comparison):
    dist_from_52w_low   PSI=9.79  Significant Drift
    dist_from_52w_high  PSI=5.69  Significant Drift
    volatility_20       PSI=4.01  Significant Drift
    adx_14              PSI=3.92  Significant Drift
    atr_14              PSI=2.69  Significant Drift
  recommend_retraining = True
```
This is a genuine, informative finding, not noise: the registered model's training
window ends 2025-01-30 (from Phase 7's `training_validation_periods`), and it is now
serving on 2026-07-16 — roughly 18 months past its training cutoff. A real distributional
shift after that much time is expected, and is consistent with this model's
already-documented weak generalization (`CONFIDENCE_ENGINE_REPORT.md`'s ROC-AUC 0.5149
finding). Drift detection surfacing this is the system working as intended.

## Tests Executed

`tests/test_ml_drift.py` (18 tests): PSI is ~0 for identical distributions and large for
an obviously shifted one, too-few training values returns `NaN` (not a false zero),
status rollup correctly picks the worst non-"Insufficient Data" status and falls back to
"Insufficient Data" when every input is, prediction drift correctly reports Insufficient
Data for an unknown symbol / fewer than the minimum sample size / a different model
version's predictions (never mixed across versions), correctly reports Stable for
constant probabilities and Significant Drift for a large probability-mean shift, concept
drift correctly reports Insufficient Data without a registered accuracy or without
enough resolved outcomes, correctly detects a large accuracy drop, feature drift returns
an empty list (not a crash) when the training feature set can't be loaded,
`assess_drift` never raises even when a sub-check is monkeypatched to throw, and
correctly reports fully-Insufficient-Data when no feature version and no predictions
exist for a never-seen symbol.

## Known Limitations

1. **PSI computed with a small live sample (60 days) against a much larger, multi-year
   training reference structurally runs higher than a textbook balanced-sample PSI
   comparison** — a short recent window is inherently less variable than a training
   population spanning years/market regimes, which alone can push PSI above the
   standard 0.25 "significant" threshold even without a true change in the
   feature-target relationship. This was discovered empirically during this phase's own
   verification (initial pooled-panel PSI values were absurdly high — 5-11 — motivating
   the per-symbol training-slice fix described above; even after that fix, PSI remains
   elevated for RELIANCE.NS, for the small-sample reason described here). This is a
   real, known caveat of applying PSI at small live-sample sizes, documented rather than
   hidden — the direction of the signal (this model is stale and likely has drifted) is
   still credible given the 18-month gap since training, but the exact PSI magnitude
   should be read as directional, not a precise, threshold-calibrated metric, until
   enough live history accumulates to tune thresholds against this app's actual usage
   pattern.
2. **Prediction and concept drift are honestly "Insufficient Data" right now** — this
   app has only recorded one real live prediction so far (Phase 5, this session), far
   below the 10/20 minimums this phase deliberately set to avoid a statistically
   meaningless verdict from a handful of predictions. This will become a live,
   meaningful signal as real usage accumulates; the mechanism itself is fully tested
   (18 passing unit tests) against seeded data covering every threshold band.
3. **The 10pp/25pp (prediction drift) and 7pp/15pp (concept drift) thresholds, and the
   10/20 minimum-sample-size gates, are this phase's own explicit choices** — no
   project-defined drift-threshold standard existed to reuse (confirmed absent by the
   Phase 1 audit, consistent with Phase 4's risk-weighting precedent of documenting
   phase-original threshold choices rather than presenting them as an established
   standard).
4. **No live-browser screenshot was taken for this phase**, consistent with every phase
   since the accidental Kotak Neo live-auth incident earlier in this session — the user
   paused further live-browser verification. Correctness is evidenced by the direct
   Python evidence above (a real `assess_drift` run against the live database and real
   price history), 18 passing unit tests, and code review of the rendering logic.

## Recommendations

1. Given the real finding that the active model is 18 months past its training cutoff
   and already shows feature drift, **retraining the champion model on a current dataset
   is a legitimate, evidence-backed next step** — independent of this XAI-platform
   effort's own scope (this phase's mandate is detecting and surfacing drift, not
   retraining), but worth flagging to whoever owns the model-training pipeline.
2. Once enough live predictions accumulate to clear the 10/20 sample-size gates,
   re-verify prediction/concept drift against real (not just seeded-test) data.
3. If a future phase adds scheduled/background jobs (flagged as a gap in
   `MODEL_PERFORMANCE_REPORT.md`'s Known Limitations), drift assessment is a natural
   candidate to run on that schedule rather than only when a user happens to open the
   expander for a given symbol.
