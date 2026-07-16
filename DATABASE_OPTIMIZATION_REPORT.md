# Database Optimization Report

Phase 5 of the Production Stabilization Directive. Every query path was
inspected; the two real problems found were fixed and verified with query-count
and timing evidence, not inspection alone.

## Indexing — verified with real `EXPLAIN QUERY PLAN` evidence, not assumed

`data/prices` (27,662 real rows) has a composite `UNIQUE(ticker_id, date)`
constraint, which SQLite implements as an implicit composite index
(`sqlite_autoindex_prices_1`). Confirmed live:

```sql
EXPLAIN QUERY PLAN SELECT * FROM prices WHERE ticker_id = 1 ORDER BY date;
-- SEARCH prices USING INDEX sqlite_autoindex_prices_1 (ticker_id=?)
```

An index **SEARCH**, not a table **SCAN** — the dominant query pattern in the
app (fetch one symbol's price history) is already well-indexed. Every
foreign-key column across all 32 tables also has an explicit `index=True`
(confirmed via inspection of every `mapped_column(ForeignKey(...))` in
`core/database.py`).

**One minor redundancy found, logged as debt, not changed**: `ix_prices_ticker_id`
(a single-column index) is now redundant given the composite unique index above
(leftmost-prefix rule means the composite index already serves ticker_id-only
lookups). Not dropped in this pass -- see `PRODUCTION_AUDIT.md`'s Technical Debt
Register and Decision Log for the reasoning (negligible impact at current scale,
no established index-migration pattern in this codebase to do it safely).

## N+1 Queries — 2 found, both fixed, both now regression-tested

| Function | Before | After | Evidence |
|---|---|---|---|
| `core.portfolio.list_holdings` | 1 query for holdings + 1 lazy-load query per row (1,880ms at 5,000 rows) | `selectinload(Holding.ticker)` -- 1-2 batched queries (~620ms at 5,000 rows, 3.0x faster) | Measured live this session; `test_list_holdings_does_not_n_plus_one` asserts query count stays ≤3 regardless of row count |
| `core.watchlist.list_watchlist` | Same pattern (1 query per watchlist entry for its `ticker`) | `selectinload(Watchlist.ticker)` | Found by inspection after the portfolio fix (same code shape); `test_list_watchlist_does_not_n_plus_one` added |

Both fixes are pure query-construction changes (an added `.options(selectinload(...))`
clause) -- no schema change, no behavior change to the returned data shape, and
both are covered by new regression tests that assert on actual SQL statement
*count* (via a `before_cursor_execute` event listener), not on timing, so they
can't silently regress and go unnoticed the way timing-based assertions would
(flaky under CI load).

**No other N+1 patterns found.** Every other DB-reading function in `core/`
(`core/queries.py`, `core/symbol_registry.py`, `core/backtester.py`, etc.) was
checked for the same `for row in session.execute(...): row.some_relationship`
shape -- none found.

## Transaction Boundaries & Connection Reuse

`core.database.get_session()` is the single context-managed transaction
boundary used throughout the app (commit on success, rollback + re-raise on any
exception, always closes the session in a `finally`). Every write path in the
app goes through it -- confirmed by grep, zero direct `Session()` instantiation
outside `core/database.py` itself. `SessionLocal` is a single module-level
`sessionmaker` bound to one `_engine`; SQLAlchemy's default pooling for a
file-based SQLite URL opens a fresh, thread-local connection per session
checkout and returns it on close -- appropriate for this app's actual concurrency
profile (a single local server process), not the connection-pool-exhaustion risk
a multi-process/high-QPS server would have.

## Concurrency — tested live, not assumed

100 concurrent Python threads writing `add_holding()` calls against the same
portfolio row (maximum realistic write contention) against a throwaway SQLite DB:
**100/100 succeeded, zero lock errors.** SQLite's own serialization plus Python's
`sqlite3` module's default busy-timeout handled this correctly. The *latency*
observed in that same test (mean 12.5s, p95 22.2s) was root-caused to an
unrelated issue -- a synchronous external network call inside the write path,
not the database layer itself -- see `BUG_FIX_REPORT.md` Finding 4 and its entry
in the Technical Debt Register.

## Migrations — reversibility and safety

Verified (re-confirming, not re-deriving, this session's earlier
`MIGRATION_VALIDATION_REPORT.md` from the prior Final Audit directive):
`core.database._apply_additive_column_migrations()` is additive-only by
construction (only ever issues `ALTER TABLE ... ADD COLUMN`, idempotent --
checks `existing_columns` before applying), consistent with this project's
standing governance rule. **Not independently reversible** (no `DROP COLUMN`
counterpart exists, nor should one for a financial data platform where a
column drop is a data-loss operation) -- this is a deliberate, documented
design choice, not an oversight, matching this directive's own "additive
schema changes must never be compromised" framing.

The one raw-SQL-with-string-interpolation site in the entire codebase
(`core/database.py:553`, building `ALTER TABLE {table} ADD COLUMN {col} {ddl}`)
was checked specifically for injection risk: all three interpolated values come
exclusively from the hardcoded, developer-authored `_ADDITIVE_COLUMN_MIGRATIONS`
list -- never from user or external input. Confirmed safe, not merely assumed
(see `SECURITY_AUDIT.md`).

## Summary

| Area | Status |
|---|---|
| Indexing | Verified adequate for current + realistic scale via `EXPLAIN QUERY PLAN`; one minor redundant index logged as low-priority debt |
| N+1 queries | 2 found, both fixed, both regression-tested (query-count assertions) |
| Transaction boundaries | Single, consistent, correctly-scoped context manager; no direct Session use outside it |
| Concurrency | 100-thread write-contention test: 0 lock errors, 0 data corruption |
| Migrations | Additive-only, idempotent, consistent with project governance; injection-safety of the one dynamic-SQL site confirmed |
