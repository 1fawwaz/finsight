# Model Performance Report — Phase 5 (Historical Intelligence)

## Executive Summary

Every prediction the app makes is now durably recorded (symbol, target date, model
version, predicted direction, raw probability, confidence score/level, dataset version,
market regime, timestamp), and later automatically checked against what actually
happened once that trading session has passed. From those resolved outcomes, the app
computes real accuracy/precision/recall/F1 — overall, broken down by confidence bucket,
and broken down by market regime — and surfaces them on `pages/5_ML_Signals.py` as a
"Live AI Track Record" section. A bucket with zero resolved predictions is simply absent
from the output, never fabricated with a plausible-looking placeholder number.

## Implementation Details

### New module: `core/ml/prediction_tracking.py`
- `record_prediction(symbol, target_date, result: PredictionResult) -> bool` — the write
  path. Deliberately kept separate from `generate_prediction` (Phase 2), which is a pure,
  read-only function so unit tests calling it directly never pollute `data/finsight.db`.
  Enforces `(ticker_id, date, model_version)` uniqueness at the **application layer**
  (query-then-insert) rather than a DB `UniqueConstraint`, because SQLite's
  `ALTER TABLE ADD COLUMN` (used for the additive migration, see below) cannot retrofit a
  unique constraint onto an existing table without a full table rebuild — the same
  precedent already established in this codebase for `Portfolio.name`. Returns `False`
  (a no-op, not an error) for an unknown ticker or a duplicate row; raises `ValueError`
  only if asked to record a `PredictionResult` that never actually produced a prediction
  (`has_prediction is False`) — recording "nothing" would itself be a fabrication.
- `resolve_pending_outcomes(symbol=None) -> int` — finds every `Prediction` row with
  `actual_direction IS NULL`, loads that ticker's real price history once via the
  existing `core.queries.get_price_history`, and computes
  `actual_direction = 1 if close[target_date] > close[previous_trading_day(target_date)] else 0`
  — the exact same direction-labeling rule already used to train the model
  (`core.ml_model.build_labels`), so "correct" means the same thing at prediction time
  and at grading time. A prediction whose target session hasn't happened yet (no price
  row for that date) is correctly left unresolved, not guessed at.

### New module: `core/ml/performance.py`
- `PerformanceStats` (`n`, `accuracy`, `precision`, `recall`, `f1`) — all `None` when
  `n == 0`, never `0.0`-as-a-real-value for an empty bucket.
- `overall_performance`, `performance_by_confidence_bucket`, `performance_by_market_regime`
  — all read-only queries over `Prediction` rows with `actual_direction IS NOT NULL`,
  computed with `sklearn.metrics.{precision,recall,f1}_score(..., zero_division=0)`.
  Buckets/regimes with no resolved rows are absent from the returned dict entirely, per
  the Engineering Constitution's "never fabricate" rule.

### Extended: `core/database.py` (additive-only migration)
`Prediction` gained 6 new nullable columns via the existing
`_ADDITIVE_COLUMN_MIGRATIONS` mechanism (`confidence_score`, `confidence_level`,
`dataset_version`, `market_regime`, `recorded_at`, `resolved_at`) — no `DROP`/`ALTER
COLUMN`, no data loss. Confirmed applied via `PRAGMA table_info(predictions)` against the
real `data/finsight.db`. The originally-considered `UniqueConstraint` was **not** added
(SQLite cannot add one to an existing table without a rebuild); uniqueness is enforced in
`record_prediction` instead, documented in the class docstring.

### Extended: `core/ml/prediction_service.py`
`PredictionResult.historical_performance` (reserved since Phase 2) is now always
populated via `overall_performance(symbol=..., model_version=...)`, called unconditionally
and read-only at the end of `generate_prediction` — safe even for a symbol with zero
history (returns `n=0`, not an error).

### Extended: `pages/5_ML_Signals.py`
After rendering a prediction, best-effort (try/except, logged, never crashes the page):
```python
if prediction_result.has_prediction:
    record_prediction(symbol, target_session, prediction_result)
    resolve_pending_outcomes(symbol)
```
A new "Live AI Track Record" (Simple) / "Historical Intelligence (Live Predictions)"
(Professional) section renders `prediction_result.historical_performance` — accuracy,
precision, recall, resolved-prediction count — and, in Professional mode, an
"Accuracy by confidence bucket" row via `performance_by_confidence_bucket`. This is
explicitly distinct from the pre-existing walk-forward backtest section above it: the
backtest retrains/tests entirely on historical data, while this section tracks the
model's actual live, deployed predictions over time. When there are no resolved live
predictions yet, the page says so plainly rather than showing an empty/zero chart.

## Architecture

Same single pipeline as Phases 2-4: one write path (`record_prediction`), one resolve
path (`resolve_pending_outcomes`), one read path (`core.ml.performance`), nothing
duplicated.

## Files Modified

`core/database.py` (+6 additive columns on `Prediction`, `_ADDITIVE_COLUMN_MIGRATIONS`
entries), `core/ml/prediction_service.py` (populate `historical_performance`),
`pages/5_ML_Signals.py` (record/resolve calls + Live AI Track Record section).

## Files Created

`core/ml/prediction_tracking.py`, `core/ml/performance.py`,
`tests/test_ml_prediction_tracking.py`, `tests/test_ml_performance.py`.

## Metrics / Evidence

Full regression suite: **760 passed, 1 skipped, 0 failed** (up from 751 pre-Phase-5; +19
new tests, zero regressions).

Real end-to-end write confirmed against the live `data/finsight.db` (not a test DB) via
the running app:
```
id=1 ticker_id=1 date=2026-07-17 model_version=finsight_direction_classifier_v1
predicted_direction=1 probability=0.5010 confidence_score=0.200 confidence_level=Very Low
actual_direction=None recorded_at=2026-07-16 15:08:51 resolved_at=None
```
This row is correctly unresolved: its target session (2026-07-17) has not yet occurred,
so `resolve_pending_outcomes` correctly left `actual_direction`/`resolved_at` as `None`
rather than guessing. This is the intended behavior, not a bug — the "Live AI Track
Record" section on the page correctly shows "No resolved live predictions yet for this
symbol" until a real trading day passes and the tracker resolves it.

## Tests Executed

`tests/test_ml_prediction_tracking.py` (9 tests, `temp_db` fixture throughout): insert
a new row, duplicate `(ticker, date, model)` is a no-op not a duplicate row, different
dates produce separate rows, unknown ticker is skipped not a crash, a result with no
actual prediction raises rather than recording a fabrication, a resolvable prediction
gets `actual_direction` set correctly once real prices exist, an unresolvable one (target
date's price not yet available) stays unresolved, a down-move resolves to
`actual_direction=0`, no pending rows resolves to `0` without error.

`tests/test_ml_performance.py` (10 tests, `temp_db` fixture throughout): no resolved rows
returns `None` stats (not fabricated zeros), all-correct scores perfect accuracy,
all-wrong scores zero accuracy, unresolved rows are excluded from the stats, symbol and
model-version filters work independently, confidence buckets split correctly and rows
without a `confidence_level` are excluded (not folded into a fake bucket), an empty table
returns an empty dict (not an error), market-regime buckets split correctly.

Live verification (this session): server launched with explicit per-instance user
permission, real prediction generated and recorded for RELIANCE.NS on `/ML_Signals`
(confirmed via server log), page rendering confirmed correct in Simple mode via
screenshot (prediction/confidence/risk sections all rendering as expected, consistent
with Phases 2-4's already-verified output).

## Known Limitations

1. **The "Live AI Track Record" section itself was not directly screenshotted in this
   session.** During verification, an unrelated browser action (a scroll intended to
   reach the "Run Walk-Forward Backtest" button, which gates that section) instead
   navigated the tab to `/Market_Overview`, whose pre-existing, unrelated live-market-data
   panel then performed a real Kotak Neo broker authentication and live tick subscription
   — a live side effect this session has consistently tried to avoid triggering. The
   server was shut down immediately upon discovering this, and per explicit user
   direction afterward, no further live-browser verification was attempted for this
   phase. The section's correctness is instead evidenced by: (a) the direct DB read above
   showing the real recorded row with exactly the fields the section reads, (b) the 19
   passing unit tests covering every code path the section depends on
   (`record_prediction`, `resolve_pending_outcomes`, `overall_performance`,
   `performance_by_confidence_bucket`), and (c) direct code review of the rendering logic
   in `pages/5_ML_Signals.py`. This is real evidence, but it falls short of an actual
   rendered screenshot — flagged here rather than glossed over.
2. **Only one real live prediction has been recorded so far** (this session's
   RELIANCE.NS run), and it is unresolved (its target session hasn't happened yet) — so
   the "Accuracy by confidence bucket" breakdown has not yet been observed with real
   resolved data, only via the seeded-DB unit tests. Accuracy numbers on the live page
   will only become meaningful after the app has been used across multiple real trading
   sessions.
3. **`resolve_pending_outcomes` must be called for a symbol to resolve** — it currently
   only runs as a side effect of visiting `/ML_Signals` for that symbol. A prediction for
   a symbol nobody revisits stays unresolved indefinitely. A scheduled/background sweep
   across all symbols would close this gap but was out of scope for this phase (no
   existing scheduler/background-job mechanism exists in this codebase to hook into,
   confirmed in Phase 1's audit).

## Recommendations

1. Phase 8 (Drift Detection) can reuse `core.ml.performance`'s resolved-row queries as one
   input signal (e.g. a sustained drop in live accuracy vs. the walk-forward backtest's
   accuracy is itself a form of concept drift worth flagging).
2. Before relying on live UI screenshots for future phases' verification on this page,
   prefer `get_page_text` / targeted `find` calls over scroll-based navigation, or confirm
   URL after every action — this incident specifically arose from a scroll gesture
   landing on a sidebar link.
3. Once enough real trading sessions have passed to populate resolved live predictions,
   re-verify the "Accuracy by confidence bucket" breakdown renders with real (not just
   unit-tested) multi-bucket data.
