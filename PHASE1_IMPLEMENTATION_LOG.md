# Phase 1 Implementation Log — Enterprise Data Platform

Spec: `../docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md` (v2.3). Governance:
`../docs/GOVERNANCE.md`. Schema target: `../docs/SCHEMA.md` "Phase 1 Target Schema".

This file is the resumability record required by the spec's Progress Tracking section —
read it first before resuming Phase 1 work in a new session. Do not restart a step marked
Complete; resume from the first Pending/In-Progress step.

## Baseline (before Phase 1 work started)

- Git: `finsight` repo, `master`, HEAD `1ccc3cf`, working tree clean.
- Tests: 346 passed (`python -m pytest -q`, venv, 2026-07-13).
- No `internal_id`, Parquet, checkpoint, manifest, symbol registry, or provider-health
  code existed anywhere (confirmed by grep across the full repo, venv excluded).

## Repository Evidence Map (Step 0 — Reconnaissance)

| Component needed | Reuse (evidence) | Extend | New |
|---|---|---|---|
| Symbol identity | `core/universe.py::resolve_symbol` (canonical `.NS`/`.BO` resolution) | — | `SymbolRegistry` model, `core/symbol_registry.py` |
| DB session/base | `core/database.py::Base`, `get_session`, `init_db` (additive `CREATE TABLE IF NOT EXISTS`) | add new models | — |
| ID/version sequencing | `core/ml/registry.py::_next_model_version` pattern (`f"{name}_v{count+1}"`), `core/ml/data_layer.py::_next_version_name` | apply same pattern to `internal_id` (`FIN-%04d`) | — |
| Retry-with-backoff | `core/ml/data_layer.py::_ingest_with_retry` (already implements spec §7.4) | reuse as-is for Symbol-Registry-aware ingestion | — |
| Ingestion/upsert | `core/data_ingestion.py::get_or_create_ticker`, `upsert_prices`, `ingest_ticker` (real `INSERT...ON CONFLICT DO NOTHING`) | re-key through `internal_id` once registry exists | — |
| Validation | `core/ml/data_layer.py::validate_symbol_history`, `SymbolQualityReport` | extend to full spec §7.8 checklist (calendar + identity checks) | `validation_log` persistence |
| NSE calendar | `core/market_status.py::is_trading_day/next_trading_day/holiday_name` (hand-maintained `_NSE_HOLIDAYS`) | reuse directly for calendar reconciliation — no new calendar source built | — |
| Universe list | `core/universe.py::load_universe` (full NSE equity snapshot CSV, **no index-membership tags**) | needs Nifty50/100/200/500 constituent membership — **gap, see Step 6 note** | index membership source |
| Dataset versioning | `core/ml/data_layer.py::MLDatasetVersion`/`create_dataset_version` | extend with constituent history + `internal_id` set | — |
| Model artifact path convention | `core/ml/registry.py` (bare filename, resolved at load time — real bug fixed here before) | reuse convention for backup/manifest paths | — |
| Backups | none — Phase 3's one backup was a manual `cp` | — | `core/backup.py`, `backup_log` |
| Checkpoint | none | — | `CheckpointState` model, `core/checkpoint.py` |
| Provider health | none | — | `provider_health` table + tracking wrapper |
| Parquet | none (`pyarrow`/`fastparquet` not in `requirements.txt`) | — | storage layer, new dependency (justification required, see Step 16) |

## Step Log

| Step | Status | Files | Tests | Commit |
|---|---|---|---|---|
| 1. Symbol Registry | **Complete** | `core/symbol_registry.py`, `core/database.py` (+`SymbolRegistry` model), `tests/test_symbol_registry.py` | 11 new, all passing | `5b4c63f` |
| 2. DB schema & migrations | **Complete** (folded into Step 1 commit — see rationale below) | `core/database.py` (+6 models: `SymbolRegistry`, `CheckpointState`, `ValidationLog`, `ProviderHealth`, `BackupLog`, `MetadataRegistry`), `core/backup.py`, `tests/test_backup.py` | 7 new (backup), migration verified live | `5b4c63f` |
| 3. Checkpoint system | **Complete** | `core/checkpoint.py`, `tests/test_checkpoint.py` | 10 (9 orig. + 1 added after Step 4's bug fix), all passing | `2ad46af` |
| 4. Historical backfill | **Complete** | `core/historical_backfill.py`, `tests/test_historical_backfill.py` | 5 new, all passing | `82bd48d` |
| 5. Incremental daily ingestion | **Complete** | `core/database.py` (+`Price.internal_id`, `_apply_additive_column_migrations`), `core/data_ingestion.py` (extended), `core/symbol_registry.py` (+`backfill_price_internal_ids`), `tests/test_ingestion.py` (+4), `tests/test_price_internal_id_backfill.py` (new, 4) | 8 new, all passing | see below |
| 6. Nifty100 support | **BLOCKED** — no authoritative index-constituent dataset (see below); skipped by explicit user direction 2026-07-13 | | | |
| 7. Nifty500 support | **BLOCKED** — same root cause as Step 6; skipped by explicit user direction 2026-07-13 | | | |
| 8. Corporate action handling | **Complete** | `core/database.py` (+`Price.dividend`/`split_ratio`), `core/data_ingestion.py` (extended), `core/corporate_actions.py` (new), `tests/test_corporate_actions.py` (new, 6), `tests/test_ingestion.py` (+2) | 8 new, all passing | see below |
| 9. Survivorship bias protection | **BLOCKED** — same root cause as Step 6; skipped by explicit user direction 2026-07-13 | | | |
| 10. Validation framework | **Complete** | `core/validation.py` (new), `tests/test_validation.py` (new, 11) | 11 new, all passing | see below |
| 11. Metadata Registry | **Complete** | `core/metadata_registry.py` (new), `tests/test_metadata_registry.py` (new, 10) | 10 new, all passing | see below |
| 12. Dataset Registry | **Complete** | `core/ml/data_layer.py` (extended `SymbolQualityReport`/`DatasetQualityReport`), `tests/test_ml_data_layer.py` (+2) | 2 new, all passing | see below |
| 13. Dataset Manifest generation | **Complete** | `core/dataset_manifest.py` (new), `tests/test_dataset_manifest.py` (new, 9) | 9 new, all passing | see below |
| 14. Provider Health monitoring | **Complete** | `core/provider_health.py` (new), `core/data_ingestion.py` (extended `fetch_price_history`), `tests/test_provider_health.py` (new, 9), `tests/test_ingestion.py` (+2) | 11 new, all passing | see below |
| 15. Backup and rollback support | **Complete** (file-copy primitive was pulled forward into Step 2; this step completes it) | `core/backup.py` (extended: `trigger` param, `backup_log` persistence, `restore_last_verified_backup`), `tests/test_backup.py` (+6) | 6 new, all passing | see below |
| 16. Parquet storage | Pending — **new dependency, needs justification per Architecture Change Rule** | | | |
| 17. Feature Store integration + Acceptance Gate | Pending | | | |

**Rationale for folding Step 2 into Step 1:** `SymbolRegistry` itself *is* a schema
addition, and the spec's own Prime Directive requires "never write to the database
without a fresh backup in place first" — so a minimal backup primitive had to exist
before Step 1 could touch the real DB at all. Rather than add `SymbolRegistry` now and a
separate empty "schema migration" commit later, Step 2's remaining schema additions
(`CheckpointState`, `ValidationLog`, `ProviderHealth`, `BackupLog`) are being added in the
same migration pass as `SymbolRegistry`, in one additive `init_db()` call, so there is one
coherent "add Phase 1 schema" migration rather than four trivial ones. The *code layers*
on top of each table (checkpoint logic, validation logic, etc.) remain separate steps and
separate commits, per the spec's "one logical improvement" rule.

## Evidence — Steps 1 & 2

- **Backup taken before touching the real DB:**
  `data/backups/finsight_phase1_symbol_registry_schema_migration_20260713_094144.db`,
  `verify_backup()` returned `True` (SQLite `PRAGMA integrity_check` passed).
- **Migration applied to the real DB, additive-only, verified via `sqlalchemy.inspect`:**
  new tables `['backup_log', 'checkpoint_state', 'metadata_registry', 'provider_health',
  'symbol_registry', 'validation_log']`; table count 13 → 19; `before.issubset(after)` is
  `True` (nothing dropped).
- **Registry backfilled from the real `tickers` table:** 20/20 existing tickers migrated
  to `symbol_registry` (20 created, 0 already present, 0 orphaned) — spec §7.7's
  retroactive-backfill requirement satisfied for the current dataset, not just new code.
- **Tests:** `test_symbol_registry.py` (11) + `test_backup.py` (7) = 18 new, all passing.
  Full suite: **364 passed** (346 baseline + 18 new), 0 regressions
  (`python -m pytest -q`, venv, 2026-07-13).
- **Known follow-up, not yet done:** the real `TATAMOTORS.NS` → `TMPV.NS` rename (a
  genuine historical event already documented in `docs/DATA_SOURCE.md`) is not yet
  annotated in the live registry, because `TMPV.NS` was ingested directly as the current
  symbol rather than via a recorded rename, and `record_rename()`'s current API always
  moves the *current* symbol into history — it has no "annotate a known predecessor
  without changing today's current_symbol" mode. Needs a small, deliberate follow-up
  function, not a rushed bolt-on now. Filed here, not silently skipped.

## Evidence — Step 3

- **Tests:** `tests/test_checkpoint.py`, 9 new, all passing, including a resumption
  simulation (`test_resume_after_interruption_skips_completed_work`) proving `remaining()`
  correctly excludes completed units after a simulated interruption.
- **Full suite:** 373 passed (364 + 9), 0 regressions.
- **Verified against the real DB:** `get_checkpoint()` created the single `id=1` row in
  the live `checkpoint_state` table (not just in-memory test DBs).

## Evidence — Step 4

- **Real bug found and fixed during this step:** `start_stage()` was unconditionally
  clearing the completed/failed lists on every call, including when re-entering the
  *same* stage -- which is exactly what a resumed loop does on every RECONNAISSANCE pass
  (spec §4). This would have silently erased all progress on every resume, defeating the
  entire point of checkpointing. Fixed to only reset on an actual stage transition;
  `test_start_stage_preserves_progress_when_resuming_same_stage` guards the fix. Caught
  by `test_backfill_universe_skips_symbol_already_completed_this_stage` failing during
  development, not found via inspection -- exactly the "run the relevant tests" step
  doing its job.
- **Scope decision:** adjusted close / dividends / splits (also listed under spec §7.2)
  are deliberately deferred to Step 8 (Corporate action handling) rather than bolted on
  here, since `prices` has no columns for them yet and adding them now would duplicate
  work `market_data` (Step 16) is meant to hold properly. Logged, not silently dropped.
- **Tests:** `tests/test_historical_backfill.py`, 5 new (incl. a resumption test proving
  a checkpointed-complete symbol's `.history()` is never re-called), all passing. One
  checkpoint test added after the bug fix (10 total in `test_checkpoint.py`).
- **Full suite:** 379 passed (373 + 6), 0 regressions.

## Evidence — Step 5

- **Design:** `Price` gained a nullable `internal_id` column (additive, no DB-level
  UNIQUE constraint — see the in-code comment on `Price.internal_id` for why one isn't
  added on a live table). `upsert_prices` gained an optional `internal_id` parameter
  (fully backward compatible — omitting it reproduces byte-identical prior behavior) that,
  when provided, also dedups across *different* `ticker_id` values sharing the same
  `internal_id` — the actual case a rename produces. `ingest_ticker`'s public signature
  is unchanged; it now resolves the Symbol Registry entry internally and passes
  `internal_id` through automatically.
- **Real regression found and fixed:** after wiring this in, `tests/test_chat.py` (9
  tests, which hit the real on-disk DB rather than an in-memory fixture) started failing
  with `sqlite3.OperationalError: no such column: prices.internal_id`. Root cause:
  `Base.metadata.create_all()` (used by `init_db()`) only issues `CREATE TABLE IF NOT
  EXISTS` — it does **not** diff columns on a table that already exists, so a new
  column on an existing model silently never reaches an already-created database. Fixed
  by adding `core.database._apply_additive_column_migrations` (an explicit,
  idempotent `ALTER TABLE ... ADD COLUMN` step, run automatically inside `init_db()`)
  rather than a one-off manual fix, so the same class of bug can't recur for a future
  additive column. This is a **repository-wide finding**, not specific to this table —
  any future additive column on an existing model needs an entry in
  `_ADDITIVE_COLUMN_MIGRATIONS` or it will silently fail the same way.
- **Migration evidence against the real DB:**
  - Backup: `data/backups/finsight_phase1_prices_internal_id_column_migration_20260713_095434.db`, verified.
  - Column added: `ALTER TABLE prices ADD COLUMN internal_id VARCHAR(32)` — confirmed via
    `sqlalchemy.inspect` before/after (`internal_id` absent → present).
  - No data loss: 22,619 `Price` rows before and after.
  - Data backfilled: `backfill_price_internal_ids()` stamped all 22,619 rows, 0 left
    unstamped, on the real DB.
- **Tests:** 8 new (4 in `tests/test_ingestion.py`, 4 in
  `tests/test_price_internal_id_backfill.py`), all passing.
- **Full suite:** **387 passed** (379 + 8), 0 regressions, including the 9 `test_chat.py`
  tests that had been failing.

## Evidence — Step 8

- **Repository evidence used:** `yfinance.scrapers.history.PriceHistory.history`'s real
  signature (`actions=True` by default) confirmed the "Dividends"/"Stock Splits" columns
  were already present in every fetch `core.data_ingestion.fetch_price_history` makes --
  just previously discarded. Captured them via the same `_ADDITIVE_COLUMN_MIGRATIONS`
  mechanism built during Step 5's regression fix (reused immediately, as intended).
- **Scope decision, stated not hidden:** full backward-adjusted price series
  recomputation is out of scope -- `core/corporate_actions.py` validates *consistency*
  (a >15% move should have a recorded action explaining it) rather than re-deriving
  yfinance's own adjustment math from scratch, which is a materially riskier, separate
  undertaking.
- **Migration evidence:** backup
  `data/backups/finsight_phase1_corporate_action_columns_migration_20260713_100608.db`,
  verified; `dividend`/`split_ratio` columns added via the additive-migration mechanism;
  22,619 `Price` rows preserved (no data loss).
- **Tests:** 8 new (6 `test_corporate_actions.py`, 2 `test_ingestion.py`), all passing.
  One test-fixture bug found and fixed during development (a hardcoded `ticker_id=1` in
  a test helper collided with the real `UNIQUE(ticker_id, date)` constraint across two
  different symbols in one test -- a test bug, not a production one).
- **Full suite: 395 passed** (387 + 8), 0 regressions.

## Evidence — Step 10

- **Reuse:** `core.ml.data_layer.validate_symbol_history` (schema/duplicate/range/outlier
  detection, Phase 3) and `core.corporate_actions.validate_corporate_action_consistency`
  (Step 8) are called directly, not reimplemented -- `core/validation.py` maps their
  results onto the closed `check_name` enum and adds the two genuinely new checks
  (calendar reconciliation, symbol identity), reusing `core.market_status.is_trading_day`
  for the calendar source rather than building a second one.
- **Tests:** 11 new, all passing, one per check plus an "every check_name gets logged"
  test and a no-data-doesn't-crash test.
- **Full suite: 406 passed** (395 + 11), 0 regressions.
- **Real finding from running against live data (RELIANCE.NS, internal_id `FIN-0001`),
  not fabricated, not silently hidden:**
  - `missing_date_calendar` failed: 62 gaps 2021–2025 (e.g. `2021-07-21`, `2021-08-19`).
    Expected and already documented: `_NSE_HOLIDAYS` only has published data for 2026
    (see `docs/DATA_SOURCE.md`'s "Partially Active" note on the calendar), so real NSE
    holidays in 2021–2025 register as weekday-only "missing" data. Not a data quality
    bug -- a known calendar-coverage gap now made visible and measurable for the first
    time, which is the validation framework doing its job.
  - `calendar_consistency` failed: real ingested rows exist on `2026-05-01`
    (Maharashtra Day), `2026-05-28` (Bakri Id), `2026-06-26` (Muharram) -- all three
    *are* in `_NSE_HOLIDAYS[2026]`. This means either (a) yfinance returned bars on
    dates NSE's own equity segment was closed, (b) one or more of these are actually
    partial/special trading sessions (the spec explicitly calls out "Muhurat trading" as
    a case the calendar doesn't yet distinguish from a full closure), or (c) a holiday
    date in the hand-maintained table is inaccurate. **Not resolved here** -- flagged as
    a genuine open finding for follow-up investigation, not guessed at or silently
    dropped from the report.

## Evidence — Step 11

- **Reuse:** thin rollup over `Price` (row counts/dates), `SymbolRegistry` (exchange
  derived from the current symbol's suffix), and `ValidationLog` (Step 10 -- the latest
  run's checks determine `validation_status`) -- no new source of truth introduced.
- **Bug found in a test, not production:** `refresh_metadata` upserts and returns the
  *same* ORM instance on a second call for the same `internal_id` (mutates in place, per
  the spec's own "idempotent, always reflects current state" requirement) -- an initial
  test compared `first.checksum != second.checksum` where `first`/`second` were the same
  object, always trivially equal after mutation. Fixed by capturing the checksum value
  immediately after each call rather than holding a reference to the mutable row.
- **Tests:** 10 new, all passing.
- **Full suite: 416 passed** (406 + 10), 0 regressions.
- **Real evidence against the live DB:** refreshed metadata for all 20 real symbols.
  Row counts range 166–1238 (the two thin ones, `FIN-0014`/`FIN-0016`, correctly match
  the two symbols already documented as excluded from ML training in
  `finsight/SESSION_STATE.md` §7 for insufficient history). Only `FIN-0001`
  (RELIANCE.NS) shows `validation_status=passed` -- correctly reflects that only that
  symbol has had `run_full_validation` actually run against it so far (Step 10's live
  test); the other 19 correctly show `not_validated` rather than a fabricated status.

## Evidence — Step 12

- **Reuse, extend, no duplication:** `core.ml.data_layer.create_dataset_version`,
  `SymbolQualityReport`, and `DatasetQualityReport` (Phase 3, existing) were extended in
  place -- both dataclass additions default to backward-compatible values, so no
  existing caller breaks.
- **Constituent history handled honestly:** rather than adding a column/field for data
  that fundamentally can't exist yet (blocked, Steps 6/7/9), the manifest JSON states
  `"constituent_history": "not_available -- blocked pending..."` explicitly. A reader of
  a dataset version's quality report can never mistake this version for
  survivorship-bias-safe.
- **Tests:** 2 new, both passing; all 14 pre-existing `test_ml_data_layer.py` tests
  still pass unchanged.
- **Full suite: 418 passed** (416 + 2), 0 regressions.

## Evidence — Step 13

- **Bug found in a test, not production:** `_seed_symbol` test helper created `Price`
  rows without stamping `internal_id` (unlike real ingestion via `upsert_prices`), so
  `refresh_metadata` found 0 matching rows and freshness silently computed as 0. Fixed
  the test helper to resolve the registry entry first and stamp every seeded row, the
  same order real ingestion follows.
- **Tests:** 9 new, all passing.
- **Full suite: 427 passed** (418 + 9), 0 regressions.
- **Real evidence against the live DB:**
  - Generated a manifest for the pre-existing Phase 3 `dataset_v1` --
    `data/manifests/dataset_v1_manifest.json` -- and it honestly shows
    `"internal_ids": []` and `freshness: 0.0`, because that dataset version's
    `quality_report_json` was written by Phase 3 code, before Step 12's
    `included_internal_ids` field existed. **Not patched retroactively** -- an old
    record correctly doesn't have data that didn't exist when it was created; this is
    surfaced, not silently backfilled with fabricated data.
  - Created a **new** `dataset_v2` (5 symbols, current code path) and generated its
    manifest: `internal_ids` fully populated (`FIN-0001`..`FIN-0005`), quality score
    composite **100.0** (completeness/integrity/coverage/freshness/corporate_action_validation
    all 100.0) -- real, verified, checksum `sha256:0d5af5551f...`.

## Evidence — Step 14

- **No schema migration needed:** `provider_health` was already created in Step 2's
  batch migration -- this step is purely the recording/query code layer on top of it.
- **Design:** `fetch_price_history` (the app's one real external-data call site) is
  extended, not duplicated, to record success/latency or failure/latency/classified
  `failure_type` around the existing `yf.Ticker(...).history()` call. A provider-health
  logging failure is caught and logged, never allowed to mask the real fetch outcome --
  the function's actual contract (return a DataFrame, or raise on a genuine fetch
  problem) is unchanged.
- **Tests:** 11 new (9 `test_provider_health.py`, 2 `test_ingestion.py` integration
  tests covering both the success and failure path), all passing.
- **Full suite: 438 passed** (427 + 11), 0 regressions.
- **Real evidence:** ran `ingest_ticker("RELIANCE.NS")` against the live DB (a genuine
  yfinance network call) -- inserted 1 new incremental row, and
  `summarize_provider_health` correctly reported `window_calls=1`,
  `success_rate=100.0`, `latency_p50_ms=2280` (a real measured latency), and a current
  `last_successful_sync` timestamp.

## Evidence — Step 15

- **Completes the primitive from Step 2:** `create_backup`/`verify_backup`/
  `restore_backup` already existed; this step adds `trigger` (closed 2-value enum),
  `backup_log` persistence for every backup taken (bare filename, matching the
  `MLModelRegistry.artifact_path` portability convention), `restored_at` tracking, and
  `restore_last_verified_backup()` (spec §7.13's rollback path).
- **Test isolation fix along the way:** existing `test_backup.py` tests didn't patch
  `core.database.SessionLocal`, so the new `backup_log` write inside `create_backup`
  would have silently hit the *real* on-disk DB during test runs. Added the `temp_db`
  fixture to every test that now triggers a `backup_log` write, keeping test data out of
  the real database.
- **Tests:** 6 new, all passing.
- **Full suite: 444 passed** (438 + 6), 0 regressions.
- **Real evidence:** took a real backup against the live DB with
  `trigger="schema_migration"`; `backup_log` correctly shows exactly **1** row (not
  retroactively fabricated for the several backups taken earlier this session, before
  this step's wiring existed) with the right trigger, bare filename, `verified=True`,
  `restored_at=None`.

**Open blocker for Step 6/7 (Nifty100/500):** `core/universe.py`'s bundled
`nse_equity_list.csv` is NSE's full listed-equity snapshot with no index-membership
column — it answers "is this a valid NSE symbol" but not "is this in the Nifty100 today,
or was it in the Nifty100 on 2024-03-01." Spec §7.1 requires constituents to be "always
resolved live from an authoritative source, never hardcoded" and §7.6 requires
point-in-time constituent history. No such source is wired into the repo today. Per the
spec's own Architecture Change Rule and Hard Stop conditions, sourcing this is a real
open item, not something to silently hardcode — logged here rather than guessed around.
