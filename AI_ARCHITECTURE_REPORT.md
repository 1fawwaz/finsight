# AI Architecture Report — Phase 1 Repository Audit

**Purpose:** document, with evidence, exactly what FinSight's existing ML/prediction
system already does before extending it into an explainable AI (XAI) platform. Every
claim below is backed by a file:line citation, not inference from filenames. This
report exists specifically so later phases never rebuild something that already works.

## Executive Summary

FinSight already has a genuinely production-grade ML pipeline — dataset/feature
versioning, a model registry with lineage, an Optuna multi-family training harness, a
mandatory generalization gate with an auto-correction loop, offline SHAP/feature
importance/calibration/confusion-matrix evaluation, experiment tracking, and
feature-importance drift monitoring. None of this needs to be rebuilt.

What's missing is entirely on the **serving side**: the live prediction path
(`core/ml_model.py::predict_next_direction`, called from `pages/5_ML_Signals.py`) uses
none of it. It trains a bare in-app `RandomForestClassifier` per request (or, when a
registry model exists, calls it directly) and returns a raw, uncalibrated
`(direction, probability)` tuple with zero lineage, zero per-prediction explanation, no
risk framing, and no persisted outcome to measure accuracy against. The Engineering
Constitution's 10 questions are almost entirely unanswered at the point a prediction is
actually shown to a user — even though 8 of those 10 questions could already be
answered today by data that exists somewhere in this codebase but is never read at
serving time.

This is a wiring and serving-layer problem, not a modeling problem.

## 1. Current Prediction Flow

`pages/5_ML_Signals.py` is the only UI surface for predictions:

1. User picks a symbol → `_load_history` (`get_price_history`, cached 900s).
2. `_predict_next(symbol)` → `core.ml_model.predict_next_direction(history, sentiment)`.
3. Result rendered as a `st.metric` (Up/Down + raw probability %) plus a hand-written
   plain-language `Explanation` from `core/explain.py::explain_ml_prediction`
   (`pages/5_ML_Signals.py:96-118`).
4. Separately, a **"Run Walk-Forward Backtest" button** triggers
   `core.backtester.walk_forward_backtest`, which trains its own fresh model per fold
   (via `core.ml_model.train_model`, the *same* simple RandomForest, not the registry
   model) and shows accuracy/precision/recall/confusion matrix/equity curve
   (`pages/5_ML_Signals.py:120-193`).
5. `historical_accuracy` shown next to the live prediction is **hardcoded to `0.55`**
   until the user manually clicks the backtest button for that session
   (`pages/5_ML_Signals.py:102`: `historical_accuracy = st.session_state["ml_result"].accuracy if has_backtest else 0.55`)
   — this is exactly the "confidence must come from the model, not hardcoded" violation
   the new Engineering Constitution explicitly forbids.

**These are two entirely separate model-training code paths** for the same page: the
live single-prediction path (registry-first, in-app-RandomForest fallback) and the
backtest path (always in-app RandomForest, ignores the registry entirely). This
divergence is itself a latent inconsistency worth fixing during Phase 2+ rather than
compounding.

## 2. Feature Engineering Pipeline

Three separate, versioned generations exist, all no-lookahead (verified by a dedicated
regression test, `tests/test_ml_model.py::test_features_have_no_lookahead`):

| Function | Feature count | Used by |
|---|---|---|
| `core.ml_model.build_features` | 9 (`lag_return_1/2/3/5`, `volume_zscore`, `rsi_14`, `macd`, `macd_signal`, `volatility_20`, +optional `sentiment`) | Live serving fallback path, `core.backtester` |
| `core.ml.feature_pipeline.build_features_v2` | 27 (the 9 above + 18: ATR/ADX/VWAP-distance/Bollinger %B & bandwidth/SMA-EMA distance/ROC/momentum/volume ratio/gap %/candle anatomy/support-resistance distance/52-week-range distance) | Registered champion model (`dataset_v1`/`features_v1`) |
| `core.ml.feature_pipeline.build_features_v3` | 34 (v2 + 7 more: rolling return mean, momentum_20, drawdown_20, rolling Sharpe, price z-score, return autocorrelation, volume percentile) | Evaluated in Phase 2 (feature selection, calibration, benchmark reports) — **not currently the registry's active feature set**; `features_v1` (the 27-feature v2 set) is what's actually registered |

Feature sets are versioned and persisted to a real feature store
(`ml_feature_sets`/`ml_feature_values`, tagged with a SHA-256 `pipeline_code_hash` of
the generating function's source — `core/ml/feature_pipeline.py:206-211`), so a
feature set is always traceable to the exact code that produced it. **Reuse
directly — no new feature engineering needed for this phase.**

## 3. Model Loading

`core/ml/registry.py`:
- Table `ml_model_registry` — `model_name`, `model_family`, `version` (auto-incrementing
  per model_name), `dataset_version`, `feature_version`, `hyperparameters_json`,
  `metrics_json`, `artifact_path` (bare filename, resolved per-environment — fixes a
  real bug found in Phase 3 where an absolute host path broke inside Docker),
  `git_commit_hash`, `created_at`, `is_active` (bool).
- `register_model(...)` inserts a new row and deactivates any prior active row for that
  `model_name` — **old versions are never deleted**, giving full lineage history for free.
- `get_active_model(model_name)` → `(model, entry)` or `(None, None)`.
- **No status lifecycle exists beyond `is_active`** — there is no Active/Testing/Archived
  enum. This is a real, confirmed gap for Phase 6 (Model Registry).
- **Multiple models are already supported** — the table is keyed by `model_name`, so
  registering a second model family under a different name is already possible with
  zero schema changes.

Currently exactly one model is registered: `finsight_direction_classifier_v1` (XGBoost,
`is_active=True`), per `TRAINING_REPORT.md`.

## 4. Prediction API

`core.ml_model.predict_next_direction(price_df, sentiment_by_date=None) -> Optional[tuple[bool, float]]`
is the single entry point. It tries the registry model first
(`_predict_with_registry_model`, `core/ml_model.py:88-117`) and falls back to a
freshly-trained in-app RandomForest on any failure (missing registry model, feature
mismatch, exception — logged, never raised to the caller). Returns a bare
`(predicted_up: bool, probability_up: float)` tuple or `None`.

**This is the exact function every later phase must wrap, not replace** — its
registry-first/graceful-fallback design is sound and explicitly documented as
deliberate (`core/ml_model.py:120-131`).

## 5. Historical Storage

Two candidate mechanisms exist, in very different states:

- **`predictions` table** (`Prediction`, `core/database.py:119-132`) — has exactly the
  right shape (`ticker_id`, `date`, `model_version`, `predicted_direction`,
  `probability`, `actual_direction`) for Phase 5's historical tracking requirement.
  **Confirmed dead: zero `Prediction(` insert call sites anywhere in the repo.** Nothing
  has ever written a row to this table. This is the single largest gap blocking Phase 5
  — the schema is already right, but no code path populates it.
- **`ml_training_runs`** (experiment log, not per-prediction) and
  **`ml_improvement_iterations`** track model-level history, not individual predictions
  — not a substitute for `predictions`.

## 6. Inference Pipeline

Single-request, synchronous, no batching, no queue. `predict_next_direction` runs
in-process inside the Streamlit request (`@st.cache_data(ttl=900)`-wrapped at the page
level, `pages/5_ML_Signals.py:67-71`). Registered-model inference latency was measured
in Phase 3 at p50=27.5ms/p95=28.7ms (`SESSION_STATE.md §8`) — no latency concern for
adding a synchronous explanation/risk step per prediction.

## 7. Existing Confidence Calculation

**There is no calibrated, labeled confidence system today.** What the UI calls
"confidence" (`pages/5_ML_Signals.py:107`: `f"{probability_up:.0%} confidence"`) is the
model's raw, uncalibrated `predict_proba` output, directly relabeled. Separately:

- `core/ml/calibration.py` **does** implement real probability calibration — Platt
  scaling (`CalibratedClassifierCV(method="sigmoid", cv="prefit")`), isotonic
  regression, and a hand-implemented temperature scaling, with Brier score/ECE/MCE
  metrics (`compare_calibration_methods`, evidenced in `CALIBRATION_REPORT.md`: Platt
  won on the last run, ECE 0.0426). **This is fully implemented but never called from
  the live serving path** — `predict_next_direction` never invokes it. This is a wiring
  gap, not a missing capability.
- `confidence_level` (Very High/High/Medium/Low/Very Low banding) does not exist
  anywhere in the repo (confirmed via repo-wide grep — zero matches).

## 8. Existing Explanation Logic

Two layers exist, both **not** model-internals explainability:

- `core/explain.py::explain_ml_prediction` — a hand-written template
  ("Our computer looked at how this stock behaved before and thinks it's a little more
  likely to go {direction}...") parameterized by direction/probability/historical
  accuracy. Not derived from feature values at all.
- `core/ai_explain.py` (via `render_ai_panel`) — an LLM (Gemini) narrative over a flat
  dict of already-computed numbers, with a rule-based fallback string. Never sees the
  model or its features directly.
- `core/ml/evaluation.py::generate_shap_summary` — **real SHAP, via
  `shap.TreeExplainer`**, but computed only in aggregate over an offline test set
  (mean |SHAP| per feature, model-level), never per single prediction. **No function
  anywhere in the repo takes one row and returns that row's own SHAP values.** This is
  the central gap Phase 3 (XAI) must close, and the natural way to close it is to call
  `shap.TreeExplainer` (already a dependency, already used) on a single-row input
  instead of a sampled test set — extending, not duplicating, `evaluation.py`'s existing
  SHAP usage.

## 9. Current Limitations (the actual gap list, ranked by phase)

1. **No per-prediction explanation** (Phase 3) — SHAP exists, but only aggregated.
2. **No confidence banding or live-path calibration** (Phase 2) — raw probability shown
   as "confidence"; calibration code exists but is unwired.
3. **No risk scoring at all** (Phase 4) — no volatility score, drawdown estimate, market
   regime, or risk level anywhere in the prediction path (though `core/explain.py`'s
   portfolio-level `explain_risk_level`/volatility indicators show the *pattern* to
   follow for a prediction-level equivalent).
4. **`predictions` table is defined but never populated** (Phase 5) — the single biggest
   blocker to historical accuracy tracking; the schema exists, the write path doesn't.
5. **No status lifecycle beyond `is_active`, no explicit model badge on the prediction
   UI** (Phase 6).
6. **Dataset freshness is tracked (`MetadataRegistry.last_sync`/`latest_date`) but never
   surfaced to the user anywhere** (Phase 7) — the data exists, it's just not read at
   serving time.
7. **Only feature-*importance* drift exists (between two experiment snapshots); no
   data/prediction/concept/distribution drift detector exists** (Phase 8) — and the one
   drift mechanism that does exist has a documented, unresolved threshold-reliability
   problem (`FEATURE_IMPORTANCE_REPORT.md`: 34/34 features flagged "drifted" at the
   default threshold when comparing a 600-row vs. 1,174-row snapshot — not validated as
   real drift).
8. **No recommendation layer** (Phase 9) — prediction + probability only, no holding
   period, stop-loss, or risk-framed guidance.
9. **No unified AI dashboard** (Phase 10) — the closest existing UI is
   `pages/5_ML_Signals.py`'s per-symbol view; there is no cross-cutting model
   health/drift/freshness dashboard.

## Files That Will Be Extended (not replaced) in Later Phases

`core/ml_model.py` (wrap `predict_next_direction`'s output, don't replace it),
`core/ml/evaluation.py` (add a per-instance SHAP function alongside the existing
aggregate one), `core/ml/calibration.py` (call from the live path, don't reimplement),
`core/ml/registry.py` (add a `status` column, additive), `core/database.py` (start
writing to the already-defined `predictions` table; add new tables additively for
anything Phase 4/8/9 needs that doesn't already exist), `core/explain.py`/
`core/ui_components.py` (new render functions alongside existing ones, following the
established `Explanation`-dataclass pattern), `pages/5_ML_Signals.py` (surface the new
data), plus one new page for Phase 10's dashboard.

## Tests Executed

None yet — this phase is read-only research (per the task's own Phase 1 scope: "Inspect
the repository and document"). No code was modified. `python -m pytest -q` was not
re-run since nothing changed; the last known-good baseline remains 346+ tests passing
per `SESSION_STATE.md` (now 676 per this session's most recent full-suite run during the
unrelated Kotak Neo work earlier in this session).

## Recommendations (for Phase 2 onward)

1. Build one small new module, e.g. `core/ml/prediction_service.py`, as the **single
   new orchestration point** that calls `predict_next_direction`, then layers on
   calibration, confidence banding, per-instance SHAP, risk scoring, and persists to
   `predictions` — so `pages/5_ML_Signals.py` (and any future consumer, e.g. the
   dashboard) calls one function and gets everything, rather than each page
   re-assembling five separate calls. This satisfies "maintain one prediction pipeline"
   without touching the existing registry/feature/calibration modules' internals.
2. Do not touch `core/ml/training.py`, `core/ml/generalization.py`,
   `core/ml/corrective_actions.py`, `core/ml/improvement_loop.py`, or
   `core/ml/data_layer.py` — none of the 10 phases require retraining or changing how
   models are produced, only how predictions already produced are surfaced.
3. Additive-only schema changes throughout, consistent with the project's standing rule
   — a `status` column on `ml_model_registry`, and reuse (not replacement) of the
   already-defined `predictions` table.
