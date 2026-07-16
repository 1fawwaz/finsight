# Dataset Report — Phase 7 (Dataset Intelligence)

## Executive Summary

Every prediction now answers "which dataset, how much data, how current, and can it
still be trusted" with real evidence: dataset version, dataset row count, real
training/validation date ranges, the latest market bar's timestamp, and a
Fresh/Delayed/Stale/Unknown freshness label. Freshness is computed honestly against the
NSE trading calendar already implemented in this codebase (`core.market_status`) — not
a fixed "data is N days old" threshold that ignores weekends/holidays, which would
falsely flag Monday's fresh data as "stale" every single weekend. A prediction with
meaningfully stale data now carries an explicit warning rather than silently looking as
trustworthy as a same-day prediction.

## Implementation Details

### New module: `core/ml/dataset_intelligence.py`
- `assess_freshness(latest_market_date, now=None) -> (label, trading_days_behind)` —
  the core new logic this phase adds. Computes the most recent NSE trading session
  whose end-of-day bar should already exist (reusing `core.market_status.is_trading_day`
  /`previous_trading_day`, never reimplementing holiday/weekend rules), then classifies
  the gap: `0` trading days behind → **Fresh**, `1` → **Delayed**, `2+` → **Stale**, no
  data at all → **Unknown** (never a fabricated "Fresh" default).
- `dataset_version_info(dataset_version) -> dict | None` — real lineage (row count,
  symbol count, date range, source) read directly from the already-persisted
  `MLDatasetVersion` table (Phase 1 Step 12 of this project's data pipeline). Returns
  `None` for a missing/unregistered version rather than a placeholder.
- `training_validation_periods(feature_version) -> ((train_start, train_end),
  (val_start, val_end)) | None` — real train/validation date boundaries, computed via
  the exact same chronological split already used at training/calibration time
  (`core.ml.cv.chronological_train_val_test_split`, the same function
  `core.ml.registry.fit_and_store_calibration` already calls) — never a second
  splitting implementation. Loading the full historical feature set from disk to derive
  this is too expensive to run on every prediction, so it's memoized per process via
  `functools.lru_cache` (a feature version's split boundaries never change once its
  dataset is fixed) rather than threaded eagerly through every `generate_prediction`
  call.

### Extended: `core/ml/prediction_service.py`
`PredictionResult` gained one new field, `dataset_size: Optional[int]`. Inside
`generate_prediction`, after confidence is assessed: the latest bar's timestamp is read
directly from `price_df.index[-1]` (data already in hand, no extra query), freshness is
classified via `assess_freshness`, and — when a registry `dataset_version` exists —
`dataset_size` is filled from `dataset_version_info`. This runs on **every** prediction,
including the in-app-fallback path (which has real price data to judge freshness
against even without a registered dataset version). A `Stale` result appends an explicit
warning; `Unknown` appends a warning that freshness couldn't be determined. Wrapped in
its own try/except, consistent with every other phase's fields — a lookup failure here
must never break the underlying prediction.

### Extended: `core/ui_components.py`
- A freshness caption ("🟢 Data: Fresh (as of 16 Jul 2026)") now renders for **every**
  prediction in **both** modes, right below the top confidence/probability row — this
  answers Q9 at a glance without requiring Professional mode, since data staleness is a
  trust-relevant fact even for a first-time user.
- `_render_dataset_intelligence_expander(result)` (Professional mode only, alongside the
  Phase 6 Model Registry expander): dataset row count, symbol count, dataset date range,
  source, and the real training/validation period boundaries when computable.

## Architecture

Same single pipeline as Phases 2-6: `assess_freshness`/`dataset_version_info` are called
unconditionally inside `generate_prediction`; `training_validation_periods` is called
lazily by the UI only when its expander is actually opened (consistent with Phase 6's
registry expander doing its own on-demand lookup, to keep every prediction cheap).
Nothing duplicated — dataset lineage reuses the Phase-1 `MLDatasetVersion` table and
the pre-existing chronological-split function; freshness reuses the pre-existing NSE
trading calendar.

## Files Modified

`core/ml/prediction_service.py` (+`dataset_size` field, freshness/dataset-lineage
population), `core/ui_components.py` (+freshness caption for both modes,
+`_render_dataset_intelligence_expander` for Professional mode).

## Files Created

`core/ml/dataset_intelligence.py`, `tests/test_ml_dataset_intelligence.py`.

## Metrics / Evidence

Full regression suite: **778 passed, 1 skipped, 0 failed** (up from 765 pre-Phase-7; +13
new tests, zero regressions).

Real evidence gathered directly against the live `data/finsight.db` and price history
(RELIANCE.NS, this session):
```
dataset_version=dataset_v1, feature_version=features_v1, dataset_size=18568
data_freshness=Fresh, latest_market_timestamp=2026-07-16
training_period=(2021-10-06, 2025-01-30), validation_period=(2025-01-31, 2025-10-16)
```
`dataset_size=18568` matches the real `MLDatasetVersion` row for `dataset_v1` exactly
(`row_count=18568`, confirmed by a direct read of the table). The training/validation
period was computed by actually loading `features_v1`'s full feature set and running
the real chronological split — not estimated.

A separate, pre-existing per-symbol rollup table (`MetadataRegistry`, Phase 1 Step 11)
was investigated as a possible freshness source and found to be **dead/unmaintained
code** — `refresh_metadata()` is called only from its own tests, never from the live
ingestion pipeline (`core.data_ingestion`), so its 20 real rows are stale snapshots from
a one-off historical run, not continuously updated. This phase deliberately does **not**
build on that table for freshness, since doing so would silently surface stale
staleness-tracking data. Instead, freshness is derived directly from the real,
currently-loaded `price_df` for the exact symbol/prediction being rendered — always
current by construction, with no separate table that can drift out of sync.

## Tests Executed

`tests/test_ml_dataset_intelligence.py` (11 tests): `None` input correctly classified
`Unknown` (never fabricated `Fresh`), a same-day post-close bar is `Fresh`, a one-day-old
bar is `Delayed`, a week-old bar is `Stale`, a bar from the prior trading session is
correctly `Fresh` when checked *before* today's market close (today's own bar can't
exist yet), a Saturday check correctly expects Friday's bar as fresh (weekend-aware, not
a naive "days since" count), `dataset_version_info` returns `None` for a missing/
unregistered version and real lineage for a registered one, `training_validation_periods`
returns `None` for a missing feature version and `None` (not a crash) when the
underlying feature set can't be loaded.

`tests/test_ml_prediction_service.py` (+2 tests): every prediction populates
`data_freshness`/`latest_market_timestamp` with a real, non-`None` value; a `Stale`
result always carries a matching warning string.

## Known Limitations

1. **Freshness before market close on a trading day is measured against the *previous*
   completed session, not intraday progress** — this is a daily/EOD-only pipeline (no
   intraday bars are ever stored), so this is the honest ceiling of "fresh" for this
   app, not a gap to close. Documented explicitly in the module docstring and covered by
   `test_before_market_close_expects_previous_days_bar_as_fresh`.
2. **`MetadataRegistry`'s staleness is a pre-existing gap this phase discovered but did
   not fix** — it's unrelated to prediction correctness (nothing in the XAI pipeline
   reads it) but is flagged here rather than silently ignored, since a future maintainer
   might otherwise assume it's a live freshness source. Wiring `refresh_metadata()` into
   the real ingestion pipeline would be a reasonable follow-up, but is a data-pipeline
   concern outside this phase's explainability mandate.
3. **No live-browser screenshot was taken for this phase**, consistent with
   `MODEL_PERFORMANCE_REPORT.md`/`MODEL_REGISTRY_REPORT.md`'s Known Limitations — the
   user paused further live-browser verification this session after the earlier
   accidental Kotak Neo live-auth incident. Correctness is evidenced instead by the
   direct-Python evidence above, 13 passing unit tests, and code review of the rendering
   logic.
4. **`training_validation_periods` is process-memoized (`lru_cache`), not
   request-scoped** — correct for this app (a feature version's underlying dataset never
   changes after creation, so the cached split is always valid), but means the cache
   would need a manual process restart if a feature version were ever deleted and its
   name reused, which no code path in this project currently does.

## Recommendations

1. If Phase 8 (Drift Detection) needs a "when was this data last refreshed" signal
   beyond a single prediction's freshness, it should reuse `assess_freshness` per symbol
   rather than build a second staleness check.
2. Consider wiring `core.metadata_registry.refresh_metadata()` into the live ingestion
   path as a separate, small follow-up — it already has full per-symbol validation
   status/checksum tracking that nothing currently keeps current.
