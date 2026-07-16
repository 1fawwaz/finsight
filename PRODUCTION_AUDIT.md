# Production Audit

Phase 1 of the Production Stabilization Directive. Every finding below comes from
an actual command run against the live repository this session (`grep`, `ast`
parsing, `sqlite3` `EXPLAIN QUERY PLAN`, `pip check`, `git status`, live module
imports) — not inferred from memory of earlier sessions.

## Current Architecture

- **`app.py`** — Home dashboard (Streamlit multipage entrypoint).
- **`pages/*.py`** (7 files) — Market Overview, Stock Analysis, Portfolio, AI
  Sentiment, ML Signals, About, Ask FinSight AI. Thin orchestration layer only;
  business logic lives in `core/`.
- **`core/`** (32 modules) — one module per concern: `database.py` (SQLAlchemy
  ORM + session mgmt), `data_ingestion.py` (yfinance fetch/upsert),
  `queries.py` (read-side queries), `indicators.py`, `portfolio.py`,
  `search_engine.py`, `sentiment.py`, `ml_model.py` + `core/ml/` (12 submodules:
  training, CV, calibration, feature selection, generalization audits, walk-forward
  validation, experiment registry, model registry), `symbol_registry.py`,
  `universe.py`, `watchlist.py`, `backtester.py`, `formatting.py`, `theme.py` +
  `design.py`, `ui_components.py`, `chat.py`, `explain.py`, `config.py`,
  `validation.py`, `backup.py`, `checkpoint.py`, `corporate_actions.py`,
  `dataset_manifest.py`, `historical_backfill.py`, `market_status.py`,
  `market_summary.py`, `metadata_registry.py`, `parquet_store.py`,
  `provider_health.py`, `fundamentals.py`, `ai_explain.py`.
- **`core/components/stock_autocomplete/`** — custom React/TS Streamlit
  Component (the one piece of non-Python frontend in the repo), built via Vite.
- **Database**: SQLite (`data/finsight.db`), 27,662 real price rows at audit
  time, additive-only migrations via `core.database._apply_additive_column_migrations`.
- **Tests**: 56 files, 648 tests, `pytest` + `pytest-cov`.

## Repository Health

| Check | Method | Result |
|---|---|---|
| TODO/FIXME/XXX/HACK markers | `grep` across every `.py` file | **0 found** |
| Debug `print()` statements | `grep '^\s*print('` across `core/`/`pages/`/`app.py` | **0 found** |
| `console.log`/`console.debug` in frontend source | `grep` in `frontend/src/` (excl. `node_modules`) | **0 found** |
| Bare `except:` / `except: pass` | AST walk of every `ExceptHandler` node | **0 found** |
| Circular imports | AST-built dependency graph + DFS cycle detection across all 32 `core/` modules | **1 found, verified non-issue** (see below) |
| Unused imports | AST-based import/usage diff across `core/` + `pages/` + `app.py` | **16 found, fixed** (see Bug Fix Report) |
| Orphaned/unreferenced modules | cross-referenced every module name against full-repo source text | **0 found** — every `core/` module is imported somewhere |
| Hardcoded secrets | `grep` for API-key/password/secret literal patterns | **0 found**; `.env` confirmed untracked (`git ls-files .env` → empty) and gitignored |
| Dependency conflicts | `pip check` | **"No broken requirements found"** |
| Dependency pinning | `requirements.txt` inspection | All 18 dependencies pinned to exact versions (no ranges) — deterministic installs |
| Stray/artifact files | directory listing at repo root | **1 found, removed**: an empty `data;C` directory (a shell path-mangling artifact, untracked, 0 bytes, 0 files) |

**Circular import detail:** `core.universe` → `core.search_engine` → `core.universe`.
Verified **not a defect** — `core/universe.py`'s `search_universe()` imports
`search_stocks` from `core.search_engine` lazily (function-local, not module-level),
specifically to break the cycle at import time; this is a pre-existing, documented
design choice (see the docstring at `core/universe.py:85-86`), not something
introduced or missed this session. Re-verified live this session: both modules
import cleanly and `search_universe("reliance")` returns correct results.

## Technical Debt

See the Technical Debt Register at the end of this document.

## Performance Risks (identified in Phase 1, measured in Phase 4)

- `Price` table candidate hot-path query (`WHERE ticker_id = ? ORDER BY date`) —
  checked via `EXPLAIN QUERY PLAN` against the real 27,662-row table: uses
  `sqlite_autoindex_prices_1` (the implicit index from the `UNIQUE(ticker_id, date)`
  constraint) via an index **SEARCH**, not a table scan. Already well-indexed.
- Every `@st.cache_data(ttl=900)`-decorated history loader (`_load_history` in
  every page) re-fetches from SQLite once per TTL window per symbol, not once
  globally — a per-page, per-session cache, not a shared one. Not a bug (each
  page's cache is independently correct), but a repeated-computation pattern
  worth measuring in Phase 4.
- `pages/3_Portfolio.py`'s Monte Carlo simulation (500 paths) and correlation
  matrix are the two heaviest compute paths in the app — candidates for Phase 4
  profiling.

## Known Bugs (found this session, fixed — full detail in `BUG_FIX_REPORT.md`)

1. `core/sentiment.py` — a malformed `pubDate` from the news API silently fell
   back to "now" with zero log trace (a real silent failure under this
   directive's own definition). Fixed: added a debug-level log.
2. `core/ml/walk_forward.py` (2 sites) — a failed backtest/CV-fold-generation for
   one window configuration in a multi-config sweep produced `nan` results with
   no log entry explaining why. Fixed: added warning-level logs with the
   specific config and exception that failed.

## High-Risk Areas

- **`core/ml/`** — the largest, most interconnected subsystem (12 submodules),
  offline-only (not on the live request path), but the least covered by this
  session's live-browser verification. Its own extensive test suite (part of the
  648) is the primary evidence for this subsystem, not manual UI testing.
- **`core/chat.py`** — the largest single file, handling free-text intent
  parsing for the "Ask FinSight AI" page; the widest input surface in the app
  (arbitrary user text), reviewed in Phase 5b (Security Verification).
- **The custom autocomplete component** — the only non-Python code in the
  request path; a regression here silently degrades every stock-search entry
  point in the app at once (already the subject of a real, fixed bug earlier
  this session — see `PORTFOLIO_FIX_REPORT.md`).

## Dependency Graph (core/ modules, by in-degree — most depended-upon first)

| Module | Dependents |
|---|---|
| `config` | 23 |
| `database` | 15 |
| `universe` | 6 |
| `market_status` | 3 |
| `data_ingestion` | 3 |
| `ml_model` | 2 |
| `formatting` | 2 |
| `explain` | 2 |
| `indicators` | 2 |
| `portfolio` | 2 |
| `queries` | 2 |
| `corporate_actions` | 2 |
| `symbol_registry` | 2 |

`config` and `database` are, correctly, the two most foundational modules in the
codebase — consistent with their role (logging/env config and ORM session
management respectively), not a sign of inappropriate coupling.

## Implementation Plan

Proceed through Phases 2-11 in the order specified by the directive. Given this
is a hardening pass on an already-large, already-tested codebase (648 pre-existing
tests, multiple prior audit passes this session), the plan prioritizes:
1. Fix the concrete bugs found in Phase 1 immediately (done, see above).
2. Edge-case and failure-injection testing next (Phase 3), since it surfaces real
   bugs Phase 2 can then trace root causes for, rather than fixing hypothetical ones.
3. Measure performance/database numbers with actual profiling (Phase 4-5) rather
   than estimating.
4. Explicitly mark any target requiring infrastructure this environment doesn't
   have (100 concurrent sessions, 1M+ row datasets, multi-host rollback) as
   **Unverified**, per this directive's own instruction not to extrapolate from
   small-scale numbers and present that as validated.

---

## Engineering Decision Log

| Problem | Options considered | Chosen solution | Why the alternatives were rejected |
|---|---|---|---|
| `core/ml/walk_forward.py`'s two silent `nan`-on-failure sites | (a) leave as-is (already surfaces `nan` in the output structurally); (b) raise instead of catching; (c) log a warning and keep the `nan` fallback | (c) log a warning and keep the `nan` fallback | (a) fails this directive's explicit definition of silent failure (no log = silent, even if a downstream value looks "off"); (b) would abort an entire multi-config sweep for one bad config, which is worse for an exploratory research tool where partial results are still useful |
| Redundant single-column index `ix_prices_ticker_id` alongside the composite `UNIQUE(ticker_id, date)` index (which already covers ticker_id-only lookups via the leftmost-prefix rule) | (a) drop the redundant index now; (b) leave it and log as debt | (b) leave it and log as debt | At the current real scale (27,662 rows) the extra write-amplification from one redundant index is not measurable, and this project's schema changes go through an explicit additive-migration mechanism (`core.database._apply_additive_column_migrations`) that has no established pattern for index drops — doing it ad hoc outside that mechanism is a bigger process risk than the performance gain justifies right now |
| Whether to install a linter (ruff/pyflakes) for a more automated dead-code pass | (a) install ruff; (b) continue using the existing AST-based checker built earlier this session | (b) continue with the AST-based checker | The Release Freeze rule bars new dependencies without a critical-regression justification; a one-off lint pass doesn't meet that bar, and the AST-based approach already found and the team verified 16 real unused imports with zero false negatives caught in review |
| The empty, untracked `data;C` directory found at repo root | (a) leave it (harmless); (b) delete it | (b) delete it | It is empty, untracked, matches no `.gitignore` pattern (so it would get committed if anyone ever ran `git add -A`), and is unambiguously a shell-command artifact, not user data -- safe under this project's "delete only what's positively identified as junk" standard |

## Technical Debt Register

| Component/path | Issue | Impact | Priority | Recommended remediation |
|---|---|---|---|---|
| `data/prices` table indexes | `ix_prices_ticker_id` (single-column) is redundant given the composite `UNIQUE(ticker_id, date)` index already serves ticker_id-only lookups (leftmost-prefix rule) | Low at current scale (27K rows); grows with table size (extra index to maintain on every write) | Low | Drop `ix_prices_ticker_id` via an explicit, reviewed migration once the project's index-migration pattern exists; re-measure write latency before/after at real production scale first |
| Repository root | ~35 accumulated `*.md` report files from this session's prior directives (audits, UI transformation, search engine, portfolio fix reports) | Cosmetic/navigability only — no functional impact, but makes the repo root noisy for a new contributor | Low | Move completed historical reports into a `docs/reports/` archive directory in a future housekeeping pass (out of scope here: this directive's own reports must also land at repo root per its explicit deliverable paths, so consolidating now would fight this directive's own instructions) |
| `core/ml/` subsystem | Lower live-browser-verification coverage than the Streamlit-facing pages (relies on its own unit/integration test suite instead) | Medium — a regression here could ship without being caught by manual UI testing, only by CI | Medium | If a dedicated CI pipeline is added in the future, gate `core/ml/` changes on its existing test suite explicitly, since it's the correct evidence source for this subsystem (not manual testing) |
| Git working tree | 30 commits ahead of `origin/master`; all of this session's work (this directive included) is uncommitted at time of writing | Medium — a machine failure before a manual commit would lose this session's work | Medium | Commit when the user explicitly authorizes it (standing instruction this session: never commit without being asked) |
| `core/data_ingestion.py::get_or_create_ticker` | Synchronous, unbounded-timeout Yahoo Finance network call executes inside an open DB write transaction on first-ever add of any symbol; measured p95 22.2s / max 22.4s latency under 100-concurrent-thread contention (see `BUG_FIX_REPORT.md` Finding 4) | High for a "release candidate hardening" bar even though no data corruption or lock errors were observed (100/100 succeeded) — a slow/rate-limited Yahoo Finance response measurably slows down or queues database writes, and this is the exact kind of external-dependency coupling a financial platform shouldn't have inside a write path | High | Decouple metadata enrichment from ticker creation: insert the row immediately with `name`/`sector` left `NULL` (already nullable), backfill via a separate, non-blocking step. Needs its own dedicated testing pass to confirm no downstream code assumes immediate population — not attempted blind in this pass. A naive "add a timeout" fix was tested and confirmed unsafe (triggers `YFRateLimitError` immediately with yfinance 1.5.1's session handling) — do not repeat that attempt without first understanding yfinance's internal session/anti-bot mechanism |
| Portfolio/Watchlist CSV export | Theoretical CSV-injection risk if a cell value ever began with `=`/`+`/`-`/`@` and the exported file were opened in a spreadsheet program | Very low — no free-text user input reaches these exports today (only bundled NSE data / yfinance metadata) | Low | If a future field ever surfaces user-typed free text into an export, prefix any leading `=`/`+`/`-`/`@` with a `'` before writing |
| App startup latency | 3,092ms mean measured startup vs. ≤2s target, driven by the heavy ML-library import chain (catboost/xgboost/lightgbm/optuna/shap all import somewhere on the startup path even though only the ML Signals page needs them) | Medium — doesn't affect a warm/already-running server, only cold starts/restarts | Medium | Investigate lazy-importing the ML libraries so only the ML Signals/backtesting pages pay their import cost; needs a dedicated pass to verify no other page transitively needs them at import time |
