# Model Registry Report — Phase 6

## Executive Summary

Every prediction already named which model produced it (Phase 2); this phase makes that
model's full lineage inspectable. A new "Model Registry" expander on `/ML_Signals`
(Professional mode) shows the serving model's status (Active/Testing/Archived), dataset
version, feature version, registration date, git commit, every recorded hyperparameter
and evaluation metric, and — when more than one version has ever been registered for
that model name — a compact version-history list showing each version's status and
which one is currently serving. Nothing here is new data: the underlying schema
(`MLModelRegistry`) already captured all of it since Phase 3/2 of this project; this
phase's actual work was building the read path (`list_registry_entries`) and the UI
surface, since neither previously existed.

## Implementation Details

### Extended: `core/ml/registry.py`
- `list_registry_entries(model_name: str | None = None) -> list[dict]` — the one new
  function this phase adds. Unlike the pre-existing `get_active_model` (which returns
  only the single currently-active entry plus its deserialized artifact), this returns
  **every** registered version for a model name (or every model if omitted), newest
  first, as plain lineage dicts — never loads the serialized `.joblib` artifact, since
  the registry UI only needs metadata. Ordered by `id.desc()` rather than
  `created_at.desc()`: SQLite's `server_default=func.now()` has second-level
  resolution, so two registrations in the same second (a realistic case — e.g. a fast
  retrain-and-promote script) would otherwise tie and sort arbitrarily; `id` strictly
  preserves insertion order regardless.

### Extended: `core/ui_components.py`
- `_render_model_registry_expander(model_name, current_version)` — a new private
  helper, following the same `st.expander` pattern already established for
  risk/explanation in Phases 3-4. Calls `list_registry_entries`, resolves the entry
  matching the prediction's actual serving version, and renders: status (with a
  color-coded icon — 🟢 active / 🟡 testing / ⚪ archived), dataset version, feature
  version, registration timestamp, git commit (short hash), every recorded eval metric,
  every recorded hyperparameter, and — only when more than one version exists — a
  version-history list marking which one is currently serving.
- Wired into `render_prediction_result`: called automatically in Professional mode
  whenever `result.model_source == "registry"` (never shown for the in-app-fallback
  path, which by definition has no registry entry to show). Simple mode is
  deliberately unchanged — its existing "Model: X (status)" caption already answers
  "which model" at the level a non-technical user needs; a full lineage table would be
  noise there, not clarity.

## Architecture

Read-only, additive. No schema change was needed (`MLModelRegistry` already had every
field this phase surfaces — `model_name`, `version`, `model_family`, `dataset_version`,
`feature_version`, `hyperparameters_json`, `metrics_json`, `git_commit_hash`,
`created_at`, `is_active`, `status`, `calibration_temperature` — most added in earlier
phases of this project, `status`/`calibration_temperature` added in Phase 2 of this
Explainable-AI effort). This phase's only code is one new read function plus one new UI
renderer; the write paths (`register_model`, `set_model_status`) already existed and are
unchanged.

## Files Modified

`core/ml/registry.py` (+`list_registry_entries`), `core/ui_components.py`
(+`_render_model_registry_expander`, wired into `render_prediction_result`).

## Files Created

None — extended existing modules only, per the repository rule against duplicate
registries/pipelines.

## Metrics / Evidence

Full regression suite: **765 passed, 1 skipped, 0 failed** (up from 760 pre-Phase-6; +5
new tests, zero regressions).

Real registry entry read directly against the live `data/finsight.db` (not a test DB):
```
version=finsight_direction_classifier_v1, status=active, dataset_version=dataset_v1,
feature_version=features_v1, git_commit=8b8c9933, registered=2026-07-12 19:31:53,
metrics={accuracy: 0.476, roc_auc: 0.515, precision: 0.469, recall: 0.925, f1: 0.623},
hyperparameters={n_estimators: 87, max_depth: 3, learning_rate: 0.032, subsample: 0.620,
colsample_bytree: 0.531, reg_lambda: 7.340}
```
This confirms `list_registry_entries` correctly surfaces the real champion model's full
lineage exactly as stored, with no fabrication or placeholder values — including its
already-known-weak ROC-AUC (0.515), consistent with the honest finding already
documented in `CONFIDENCE_ENGINE_REPORT.md`.

## Tests Executed

`tests/test_ml_registry.py::TestListRegistryEntries` (5 new tests): empty registry
returns an empty list (not an error), a single registration's full lineage fields are
returned intact (hyperparameters/metrics round-trip through JSON correctly), multiple
versions of the same model name are returned newest-first with the superseded version
correctly marked `is_active=False` (and still present — the registry never deletes
history), omitting `model_name` returns entries across every registered model, and a
status change via the pre-existing `set_model_status` is reflected in the listing
immediately (proving the read path isn't reading stale/cached data).

Live verification: only direct-Python evidence against the real database was gathered
for this phase (see Known Limitations — the same reasoning documented in
`MODEL_PERFORMANCE_REPORT.md` applies: after the earlier accidental live Kotak Neo
authentication incident this session, further live-browser verification was explicitly
paused by the user for this work).

## Known Limitations

1. **No live-browser screenshot of the new expander was taken this phase**, for the same
   reason documented in `MODEL_PERFORMANCE_REPORT.md`'s Known Limitations — the user
   opted to skip further browser verification after the accidental Kotak Neo live-auth
   incident earlier in this session. Correctness is evidenced instead by: (a) the direct
   read against the real registry entry shown above, matching every field the renderer
   consumes, (b) 5 passing unit tests covering `list_registry_entries`'s every code path,
   and (c) direct code review of the rendering logic. This is real evidence but not an
   actual rendered screenshot — flagged rather than glossed over.
2. **Only one model has ever been registered in the real database** (`
   finsight_direction_classifier_v1`), so the "version history" list (which only renders
   when more than one version exists) has not been observed with real multi-version data
   — only via the seeded-DB unit test (`test_returns_every_version_newest_first`). It will
   render correctly the next time a second version of any model is registered and
   promoted (already covered by the existing `register_model`/promotion pipeline from
   earlier phases of this project — this phase does not change that pipeline).
3. **No UI control to change a model's status was added** — `set_model_status` already
   existed (Phase 2 of this effort) but has no UI entry point; changing a model's
   lifecycle status today requires a direct Python call. Adding a UI control for this was
   judged out of scope for an XAI/transparency phase (it's a write/admin action, not an
   explainability concern) — flagged here as a legitimate gap rather than silently
   omitted from the report.

## Recommendations

1. If a future phase (or separate task) adds model promotion/retraining automation, it
   should call the existing `register_model`/`set_model_status` functions unchanged —
   this phase's registry listing will automatically pick up new versions with no further
   changes needed.
2. If an admin UI for status changes is ever wanted, it belongs on a separate,
   access-controlled surface (not the public-facing ML Signals page) — flagged as a
   design consideration, not built here, since this phase's mandate was explainability
   surfacing, not administrative tooling.
