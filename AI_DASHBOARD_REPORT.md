# AI Dashboard Report — Phase 10

## Executive Summary

A new page, `pages/8_AI_Dashboard.py`, aggregates every prior phase's output into one
enterprise-style view for a symbol: latest prediction, confidence gauge, probability
distribution, feature importance chart, historical accuracy, prediction timeline, risk
gauge, market regime, model version, dataset version, data freshness, drift status, and
system health. It introduces **zero new prediction/risk/explanation/drift logic** — it
is purely a new rendering surface over `core.ml.prediction_service.generate_prediction`
and the other `core.ml.*` modules every other phase already built and tested, per the
project's "one prediction pipeline, one explanation engine" rule.

## Implementation Details

### New page: `pages/8_AI_Dashboard.py`
Numbered `8_` (after the existing `7_Ask_FinSight_AI.py`) rather than renumbering any
existing page — existing pages' file names are effectively stable URLs/nav slugs in a
live app, and renumbering them would be an unnecessary, disruptive change outside this
phase's scope. Structure, top to bottom:
1. **Latest Prediction** — direction/confidence/risk/freshness metric row (reuses
   `generate_prediction`'s output directly, no new computation).
2. **Confidence Gauge** and **Risk Gauge** — `go.Indicator(mode="gauge+number")`,
   following the exact same gauge pattern already established in
   `pages/3_Portfolio.py`'s volatility gauge (`theme.STATUS_GOOD/WARNING/SERIOUS/CRITICAL`
   color bands, `theme.apply_dark_layout`) — not a new charting convention.
3. **Probability Distribution** — a two-bar chart of `probability_up`/`probability_down`.
4. **Feature Importance** — a horizontal bar chart of the top 10 entries from
   `result.explanation.feature_importance_ranking` (Phase 3's real SHAP output);
   explicitly shows "no explanation available" rather than an empty chart when the
   in-app-fallback path produced the prediction.
5. **Historical Accuracy** — `result.historical_performance` (Phase 5), same
   "Insufficient Data" honesty as everywhere else when `n == 0`.
6. **Prediction Timeline** — a new read function, `core.ml.performance.prediction_timeline`
   (see below), charted as predicted-probability-over-time with correct/incorrect
   outcome markers.
7. **Model & Dataset Lineage** — model version/status, dataset version/size (Phases 6-7).
8. **Drift Status** — the full `core.ml.drift.assess_drift` report (Phase 8), computed
   on-demand exactly like the ML Signals drift expander (with a spinner, since it loads
   the training feature set on first call per feature version).
9. **System Health** — `core.ml.system_health.run_all_checks` (new, see below).

### New module: `core/ml/performance.py::prediction_timeline`
One new read function alongside Phase 5's existing `overall_performance`/
`performance_by_confidence_bucket`/`performance_by_market_regime`: every recorded
prediction for a symbol (resolved or not — a timeline should show pending predictions,
unlike the accuracy functions, which correctly exclude them), newest first, capped at a
configurable limit. Reuses the exact same `Prediction`/`Ticker` query pattern already
established in that module.

### New module: `core/ml/system_health.py`
Four checks, each reusing an existing subsystem rather than adding a new one: database
connectivity (`SELECT 1` through the existing `get_session`), active model registered
(`core.ml.registry.get_active_model`), price data available (the already-loaded
`price_df`), prediction generation succeeded (`result.has_prediction`). Every check
always runs and always returns a result — a failing check is itself the useful signal,
never a reason to skip the rest. `run_all_checks` never raises.

### Design decision: no periodic auto-refresh timer
The mission's Phase 10 spec says "everything auto-updates." This dashboard interprets
that as *always reflecting current state on every page load/rerun* — which it already
does, since `generate_prediction` is never cached and every lineage/drift lookup reads
the live database directly. It deliberately does **not** add a `st.fragment(run_every=...)`
polling timer (the pattern used for the pre-existing Kotak Neo live-tick panel), because
that pattern is only safe for a *cheap* in-memory cache read — here, a periodic rerun
would repeatedly re-trigger the expensive SHAP explanation, the training-feature-set
load behind PSI drift, and the calibration/registry lookups every few seconds, for no
benefit (none of those figures change faster than a user's own navigation). This is a
deliberate, documented interpretation, not an oversight.

## Architecture

Same single pipeline as every prior phase: the dashboard calls
`generate_prediction`, `prediction_timeline`, `assess_drift`, `list_registry_entries`,
and `run_all_checks` — five read-only entry points, all pre-existing except
`prediction_timeline`/`run_all_checks`, which are themselves thin, tested additions to
already-existing modules. No duplicated logic anywhere in this phase.

## Files Modified

`core/ml/performance.py` (+`prediction_timeline`).

## Files Created

`pages/8_AI_Dashboard.py`, `core/ml/system_health.py`,
`tests/test_ml_system_health.py`, plus `TestPredictionTimeline` appended to
`tests/test_ml_performance.py`.

## Metrics / Evidence

Full regression suite: **825 passed, 1 skipped, 0 failed** (up from 811 pre-Phase-10;
+14 new tests, zero regressions).

Every piece of the dashboard's non-Streamlit logic (chart construction, data shaping,
drift/health computation) was exercised directly in Python against real data
(RELIANCE.NS, this session) and completed without error:
```
confidence gauge OK (1 trace)
risk gauge OK
probability bar OK
feature importance chart OK (10 features)
timeline rows: 1 (correct: 0, incorrect: 0 -- the one real recorded prediction is
  still unresolved, consistent with MODEL_PERFORMANCE_REPORT.md)
timeline chart OK
drift overall: Significant Drift (consistent with DRIFT_REPORT.md's finding)
health: Database connectivity          True  OK
health: Active model registered        True  finsight_direction_classifier_v1 (active)
health: Price data available           True  1242 bar(s) loaded.
health: Prediction generation          True  Source: registry
```
`py_compile` confirms the page and every new module are syntactically valid.

## Tests Executed

`tests/test_ml_system_health.py` (9 tests): a real DB connection reports healthy, an
unregistered model name reports unhealthy, a registered active model reports healthy
with its version/status in the detail, empty/`None` price data reports unhealthy, a
populated frame reports healthy with a row count, no-prediction and `None` results both
report unhealthy, and `run_all_checks` always returns exactly four checks and never
raises even when every individual check would fail.

`tests/test_ml_performance.py::TestPredictionTimeline` (5 new tests): an unknown symbol
returns an empty list, unresolved rows are included (unlike the accuracy-only queries),
results are ordered newest-first, filtering by model version works, and `limit` is
respected.

## Known Limitations

1. **No live-browser screenshot was taken for this phase** — the most significant
   limitation to disclose honestly, since this is a brand-new page and the other
   phases' expanders had at least partial prior browser verification to build on. The
   user paused further live-browser verification earlier in this session after the
   accidental Kotak Neo live-auth incident (see `MODEL_PERFORMANCE_REPORT.md`'s Known
   Limitations for the full account), and that pause was not re-opened for this phase.
   In its place: every computational and chart-construction code path the page calls
   was exercised directly in Python against real data (shown above) and completed
   without error, `py_compile` confirms the page parses correctly, and the page reuses
   `go.Indicator`/`theme.apply_dark_layout` patterns already verified live in
   `pages/3_Portfolio.py`. This is real evidence of correctness, but it is not the same
   as an actual rendered screenshot, and this report does not claim otherwise.
2. **The page number (`8_`) was chosen to avoid disrupting existing page slugs**, not
   for any semantic reason — if this project later reorganizes page numbering, this
   page can move freely since nothing else in the codebase references it by number.
3. **The drift section's `st.spinner` can take a few seconds on first load per feature
   version** (it loads the full training feature set, memoized after) — acceptable for
   an on-demand dashboard view, but worth knowing if this page is ever embedded
   somewhere latency-sensitive.

## Recommendations

1. Prioritize a live-browser check of this specific page the next time browser
   verification resumes in this project, given it's the one phase in this whole
   Explainable-AI effort without even partial prior visual confirmation.
2. If the project later adds a background job scheduler (a gap already flagged in
   `MODEL_PERFORMANCE_REPORT.md`), this dashboard's five read calls are natural
   candidates to pre-compute on that schedule rather than on every page visit, once
   usage volume makes that worthwhile.
