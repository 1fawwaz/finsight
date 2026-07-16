# Migration Validation Report

Date: 2026-07-14. Covers directive §6 (cross-referencing §5 row counts).

## Migration mechanism

FinSight uses an additive-only migration strategy (`core.database
._apply_additive_column_migrations`, run on every `init_db()` call): `Base.metadata
.create_all()` creates any wholly new table, then an explicit, hand-maintained list
(`_ADDITIVE_COLUMN_MIGRATIONS`) issues `ALTER TABLE ... ADD COLUMN` for any column
added to an *existing* table, guarded by a check that skips tables that don't exist
yet (a fresh install creates them with the column already present via `create_all()`)
and columns already present (idempotent — safe to run on every startup).

## Schema migrated successfully

Verified directly against the live, real (not a throwaway) database:

```
Additive migrations: declared vs actually present
prices.internal_id (VARCHAR(32)): PRESENT
prices.dividend (FLOAT): PRESENT
prices.split_ratio (FLOAT): PRESENT
ml_training_runs.git_commit_hash (VARCHAR(64)): PRESENT
ml_training_runs.training_duration_seconds (FLOAT): PRESENT
ml_training_runs.prediction_latency_ms (FLOAT): PRESENT
ml_training_runs.calibration_results_json (TEXT): PRESENT
ml_training_runs.feature_importance_json (TEXT): PRESENT
ml_training_runs.notes (VARCHAR(1024)): PRESENT
portfolios.updated_at (DATETIME): PRESENT
```

All 10 declared additive migrations are present in the live schema — none missing,
none pending.

## Existing data preserved (row counts, before/after this session's own migration)

The one migration added *this session* is `portfolios.updated_at`. Its application
was observed live: before the column existed, `pages/3_Portfolio.py` raised
`OperationalError: no such column: portfolios.updated_at` (logged as the root cause
of a real bug, see `PORTFOLIO_FIX_REPORT.md`); after adding `init_db()` to that page
(the fix), the migration applied automatically and the existing `portfolios` row
count was unaffected — a portfolio created *before* the migration (a `Portfolio` row
inserted with only `id`/`name`/`created_at`) survived with `updated_at=NULL` until its
first holding change, exactly as an additive nullable column should behave.

Current live row counts (representative snapshot, not a synthetic test DB):

| Table | Row count |
|---|---|
| portfolios | 1 |
| holdings | 3 |
| tickers | 24 |
| prices | 26,425 |
| watchlist | 12 |
| ml_training_runs | 53 |
| symbol_registry | 24 |

No row count dropped unexpectedly across this session's work (confirmed by
re-querying at multiple points during the audit — see `DATABASE_INTEGRITY` evidence
in `REPOSITORY_HEALTH_REPORT.md` / `FINAL_IMPLEMENTATION_REPORT.md`).

## New columns/fields populated correctly (no unexpected nulls)

- `portfolios.updated_at`: the one real portfolio in the live DB shows
  `created_at=2026-07-13 19:56:19`, `updated_at=2026-07-13 19:58:27.670669` — a real,
  later timestamp, confirming the "bump on holding change" logic fires correctly in
  actual use, not just a default/null placeholder.
- `prices.internal_id`: 26,425/26,425 rows populated (100%) — consistent with Phase 1's
  documented backfill; not an unexpected-null situation.
- `ml_training_runs.git_commit_hash`: 1/53 rows populated — **this is correct, not a
  bug**: only rows logged after Phase 2 Step 11 (which added the column) via the new
  `log_experiment` function carry it; the 52 earlier rows correctly show `NULL`
  because that field didn't exist when they were written (documented identically in
  `EXPERIMENT_REGISTRY.md` from Phase 2, cross-checked here and still consistent).

## Backward compatibility maintained

Older rows remain valid under the new schema in every case checked: nullable additive
columns never force a fake default onto pre-existing rows, and no query in the
codebase assumes a new column is always non-null (all reads either tolerate `NULL` or
only run against rows known to have been written after the column existed, e.g.
`get_experiment_history`'s own documented treatment of the pre-Step-11 rows).

## Rollback tested where the framework supports it

The migration framework here is intentionally one-directional (additive-only, by
explicit project governance in `docs/GOVERNANCE.md` — additive-only DB schema
changes is a hard rule for this repository, not an oversight). There is no
`DROP COLUMN` tooling because the project's own standing rule forbids destructive
schema changes. This is documented, not silently assumed — see
`ROLLBACK_READINESS` in `FINAL_IMPLEMENTATION_REPORT.md` for the full rollback-path
discussion (a rollback here means restoring from a pre-migration backup, not running
a reverse migration, since none of the additive columns removed or altered anything
that existed before).

## Summary

| Item | Status |
|---|---|
| Schema migrated successfully | ✅ Verified — all 10 declared migrations present |
| Existing data preserved | ✅ Verified — row counts stable, no unexpected loss |
| New columns populated correctly | ✅ Verified — real, non-default data where expected; correctly-null legacy rows documented |
| Backward compatibility maintained | ✅ Verified |
| Rollback tested (where framework supports it) | N/A by design — additive-only migrations, explicitly documented, not reversible by design, consistent with project governance |

**Overall: Migration validation PASSES.**
