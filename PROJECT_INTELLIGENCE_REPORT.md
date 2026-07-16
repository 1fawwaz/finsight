# FinSight — Project Intelligence Report

Generated under the Zero-Trust Evidence Constitution: the repository codebase is the
sole source of truth. Every claim below cites a runtime result, a file:line reference,
or a command output. Prompt text, prior chat history, and planning specifications are
treated as declarations of intent only, never as evidence of implementation.

## Methodology note (transparency, not a finding)

Three research passes fed this report. Two ran directly against the live working
directory. The first pass (Enterprise Data Platform) was initially launched with git
worktree isolation; that worktree turned out to be checked out at a commit 30+ commits
behind the live tree, and would have produced false "not implemented" conclusions for
several real, working modules. The agent running that pass caught the divergence itself
(comparing file counts and `git worktree list`), reported it explicitly, and re-ran its
audit against the correct live directory. This is disclosed here rather than silently
corrected, per the constitution's "every contradiction is recorded" requirement — it
is a methodological contradiction *in this audit's own execution*, not just in the
subject matter.

## Repository Fingerprint

| Field | Value |
|---|---|
| Repository root | `C:\Users\DELL\Documents\FinSight\finsight` (git repo) |
| Outer project root | `C:\Users\DELL\Documents\FinSight` (hosts `docs/` and `CLAUDE.md`, not itself a git repo) |
| HEAD commit | `b3e9abc78f59b10d6a19681167150f35d3ab0e60` |
| Branch | `master` |
| Last commit timestamp | `2026-07-13T18:45:16+05:30` |
| Remote | `https://github.com/1fawwaz/finsight.git` |
| Local vs. remote | 0 commits behind, 30 commits ahead of `origin/master` (unpushed) |
| Working tree status | **Dirty** — 35 tracked files modified, 90+ untracked files (new modules, new tests, new report `.md` files) |
| Report basis | **Working-tree state, not HEAD** — the bulk of the audited implementation (Explainable-AI platform, broker adapter migration, most of the Enterprise Data Platform) exists only uncommitted |

**Evidence**: `git log -1`, `git status --short`, `git rev-list --left-right --count origin/master...HEAD` — run directly, this session.

## Repository Coverage Log

| Metric | Target / Location | Count / Value | Technical Justification |
|---|---|---|---|
| Directories inspected | Full tree, excluding `.git`/`venv`/`node_modules`/`__pycache__` | 167 | Full `find` enumeration |
| Files inspected (candidate set) | All text/source files, same exclusions | 411 | Full `find` enumeration |
| Files skipped (explicit exclusions) | `venv/` (56,460), `node_modules/` (3,903), `__pycache__/` (25,199), `.git/` | ~85,562 | Virtual env, JS package tree, Python bytecode cache, git internals |
| Ignored binaries | `.parquet` (112), `.db` (11), `.png` (4), `.joblib` (1), `.tfevents` (2) | 130 | Historical market-data cache, SQLite DB files, chart exports, a serialized model artifact — existence/row-count verified, not line-read |
| Ignored generated files | `.pytest_cache/` (6), `catboost_info/` (6), compiled frontend `build/` bundle | ~15 | Reproducible build/cache artifacts, not authored source |
| Unreadable files | — | 0 | Every file in the candidate set passed a direct readability check |
| Coverage confidence | Overall | **HIGH** for `core/`, `pages/`, `tests/`, `app.py`, `docs/`, dependency files — every file in these areas was read directly or inventoried by a research pass with cited file:line evidence and a real test run. **MEDIUM** for `data/`'s binary contents (existence/structure verified, not every row read). **N/A** for `venv/`/`node_modules/`/build output — third-party/generated, deliberately excluded. |

## Repository Execution Budget

Full inspection completed across four passes this session: (1) direct verification of
the Explainable-AI platform and broker adapter migration, (2) a research pass covering
search/portfolio/sentiment/chat/the custom React component, (3) a research pass
covering the Enterprise Data Platform (self-correcting a worktree staleness issue
mid-pass), (4) a research pass covering the offline ML training/evaluation pipeline.
**Coverage reached 100% of readable source directories relevant to this audit** — no
directory under `core/`, `pages/`, or `tests/` was silently skipped. `data/`'s binary
contents were inventoried by structure/count, not read row-by-row (see Coverage Log).

---

## Component Status — Explainable-AI Platform (`core/ml/*`, verified directly)

| File | Lines | Status | Evidence |
|---|---|---|---|
| `core/ml/confidence.py` | 81 | **[VERIFIED]** | Imported by `prediction_service.py:24`; test-backed |
| `core/ml/explanation.py` | 142 | **[VERIFIED]** | Imported by `prediction_service.py:25`; test-backed |
| `core/ml/risk.py` | 176 | **[VERIFIED]** | Imported by `prediction_service.py:27`; test-backed |
| `core/ml/prediction_service.py` | 257 | **[VERIFIED]** | Called from `pages/5_ML_Signals.py:79`, `pages/8_AI_Dashboard.py:80` |
| `core/ml/prediction_tracking.py` | 127 | **[VERIFIED]** | Called from `pages/5_ML_Signals.py` |
| `core/ml/performance.py` | 125 | **[VERIFIED]** | Called from `prediction_service.py`, both ML pages |
| `core/ml/dataset_intelligence.py` | 117 | **[VERIFIED]** | Called from `prediction_service.py`, `ui_components.py` |
| `core/ml/drift.py` | 283 | **[VERIFIED]** | Called from `prediction_service.py`, `ui_components.py`, `pages/8_AI_Dashboard.py` |
| `core/ml/recommendation.py` | 111 | **[VERIFIED]** | Called from `prediction_service.py:252` |
| `core/ml/system_health.py` | 69 | **[VERIFIED]** | Called from `pages/8_AI_Dashboard.py` |

**Runtime evidence**: combined run of all 10 modules' test files plus broker adapter tests → **258 passed, 0 failed, 7 warnings**, this session.

## Component Status — Broker Adapter Migration

| File | Lines | Status | Evidence |
|---|---|---|---|
| `core/broker_adapter.py` | 163 | **[VERIFIED]** | `get_active_broker_adapter`/`get_secondary_broker_adapter` called from `ui_components.py:455,544,578` (3 real sites) |
| `core/kotak_adapter.py` | 117 | **[VERIFIED]** | Wired via `broker_adapter.py`; test-backed |
| `core/upstox_adapter.py` | 101 | **[VERIFIED]** | Wired via `broker_adapter.py`; test-backed |
| `core/kotak_market_data.py` | 799 | **[VERIFIED]** | Real live-broker connection confirmed this session (real auth + WebSocket ticks) |
| `core/upstox_market_data.py` | 642 | **[VERIFIED]** | Real live-broker connection confirmed this session, including a real bug found and fixed live (`BROKER_ARCHITECTURE.md`) |
| `core/tick_sequence.py` | 155 | **[VERIFIED]** | Used inside `upstox_market_data.py`'s `_on_message`; test-backed |

---

## Component Status — Enterprise Data Platform

Full detail gathered by a dedicated research pass, cross-referenced against
`docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md` and `docs/SCHEMA.md` line-by-line, with
real DB row counts and real test runs.

| File | Lines | Status | Key evidence |
|---|---|---|---|
| `core/data_ingestion.py` | 280 | **[VERIFIED]** | Wired into `app.py:11` + 6 pages; imports `symbol_registry.get_or_create` + `provider_health.record_call` directly (L18-19); 29/29 tests pass |
| `core/symbol_registry.py` | 244 | **[VERIFIED]** | 5 real call sites in `core/`; **32 real rows** in `symbol_registry` table; 11/11 tests pass; field set matches `docs/SCHEMA.md` exactly |
| `core/checkpoint.py` | 104 | **[IMPLEMENTED, not reachable from the live UI]** | Called only from `core/historical_backfill.py`, which is itself not imported by `app.py`/`pages/*` (no page, no `__main__` entry point); 10/10 tests pass; 1 real row in `checkpoint_state` |
| `core/dataset_manifest.py` | 145 | **[PARTIALLY WIRED]** | Real manifest JSON files exist on disk (`data/manifests/dataset_v1_manifest.json`, `dataset_v2_manifest.json` — genuine content, e.g. quality score 77.65, not placeholders) but `generate_manifest`/`load_manifest` are called only from tests, no production call site |
| `core/metadata_registry.py` | 94 | **[PARTIALLY WIRED]** | **20 real rows** in `metadata_registry` table, but `refresh_metadata()` itself is called only from tests |
| `core/corporate_actions.py` | 102 | **[VERIFIED]** | Real production call sites in `dataset_manifest.py` and `validation.py`; 6/6 tests pass |
| `core/database.py` — 23 table classes | 632 | **[VERIFIED]** | Every table (including the 5 Phase-1 tables: `SymbolRegistry`, `CheckpointState`, `ValidationLog`, `ProviderHealth`, `BackupLog`) confirmed present at cited line numbers, populated with real rows, migration-safe (`_ADDITIVE_COLUMN_MIGRATIONS`, 19 entries, `ADD COLUMN` only) |
| `core/provider_health.py` | 114 | **[VERIFIED]** | Wired into `data_ingestion.py`'s one external call site; 3 real rows; 9/9 tests pass |
| `core/backup.py` | 145 | **[VERIFIED]** | **10 real timestamped backup files** on disk, the newest dated **2026-07-16** (3 days after the docs' "not yet started" snapshot date) — direct proof the docs are stale; 5 real log rows; 13/13 tests pass |
| `core/parquet_store.py` | 116 | **[VERIFIED mechanism] / [NOT WIRED into the default training path]** | **20 real `internal_id` partitions**, each with real per-year `.parquet` files on disk; but the live ML page (`pages/5_ML_Signals.py`) still reads the SQLite path (`core.ml_model.make_dataset`), not the Parquet path; 12/12 tests pass across 2 test files |
| Survivorship-bias / constituent-history (spec §7.6) | — | **[PLANNED / SPECIFIED ONLY]** | Code itself emits `"constituent_history": "not_available -- blocked pending an authoritative Nifty index-constituent dataset"` (`core/ml/data_layer.py`); `PHASE1_IMPLEMENTATION_LOG.md` confirms Steps 6/7/9 (Nifty100/Nifty500/survivorship) as explicitly BLOCKED by user direction — the one spec item genuinely not implemented, and honestly self-disclosed as such in the code |
| NSE-official-calendar reconciliation (spec §7.8) | `core/market_status.py`, `core/validation.py` | **[PARTIALLY IMPLEMENTED]** | Real, tested reconciliation logic, but sourced from a hand-maintained static holiday table (self-labeled `"nse_calendar": "hand-maintained"` in `dataset_manifest.py`), not a live official-NSE-calendar feed |

**Runtime evidence**: `tests/test_symbol_registry.py`, `test_checkpoint.py`, `test_dataset_manifest.py`, `test_metadata_registry.py`, `test_corporate_actions.py`, `test_provider_health.py`, `test_parquet_store.py`, `test_backup.py`, `test_ingestion.py`, `test_database.py`, `test_historical_backfill.py`, `test_validation.py`, `test_price_internal_id_backfill.py`, `test_feature_pipeline_parquet_integration.py` — all run individually this session, all passing (117 tests total across this group).

**Real DB row counts** (`sqlite3 data/finsight.db`, this session): `symbol_registry`=32, `checkpoint_state`=1, `validation_log`=220, `provider_health`=3, `backup_log`=5, `metadata_registry`=20, `market_breadth_daily`=1239, `feature_registry`=2, `feature_importance_snapshots`=68.

---

## Component Status — Core ML Training/Evaluation Pipeline (non-XAI)

A structurally important finding: **only 4 of these 18 files are reachable from a
running Streamlit page.** The remaining 13 form a self-consistent, fully-tested,
*offline* training/evaluation pipeline with zero import edge reaching `app.py` or any
`pages/*.py` file — verifiable only by direct test invocation, not by using the live app.

| File | Lines | Status | Test result |
|---|---|---|---|
| `core/ml_model.py` | 146 | **[VERIFIED]** | 11 passed — wired via `pages/5_ML_Signals.py`, `core/chat.py`, `prediction_service.py` |
| `core/ml/__init__.py` | 3 | [IMPLEMENTED] | n/a (package marker) |
| `core/ml/baseline.py` | 35 | [DISCOVERED] | 4 passed |
| `core/ml/benchmark.py` | 440 | [DISCOVERED] | 16 passed |
| `core/ml/calibration.py` | 201 | **[VERIFIED]** (partially) | 8 passed — only the `apply_calibration`/`_apply_temperature` runtime path is live-reachable (via `registry.py:230` → `prediction_service.py:135` → both ML pages); the fitting half (`fit_temperature`) is only test-reachable |
| `core/ml/corrective_actions.py` | 231 | [DISCOVERED] | 12 passed |
| `core/ml/cv.py` | 122 | [DISCOVERED] | 8 passed — most internally-reused module in the offline cluster, but no path to `app.py` |
| `core/ml/data_layer.py` | 293 | [DISCOVERED] | 16 passed |
| `core/ml/evaluation.py` | 151 | [DISCOVERED] | 6 passed |
| `core/ml/feature_importance_monitoring.py` | 123 | [DISCOVERED] | 11 passed |
| `core/ml/feature_pipeline.py` | 318 | **[VERIFIED]** | 16 + 2 passed — wired via `ml_model.py` and `prediction_service.py` into both ML pages |
| `core/ml/feature_selection.py` | 208 | [DISCOVERED] | 12 passed |
| `core/ml/generalization.py` | 159 | [DISCOVERED] | 8 passed |
| `core/ml/improvement_loop.py` | 97 | [DISCOVERED] | 6 passed |
| `core/ml/registry.py` | 235 | **[VERIFIED]** | 27 passed — most heavily-wired file in this group (called from `pages/8_AI_Dashboard.py`, `ui_components.py` → 8 pages, `ml_model.py`, `prediction_service.py`) |
| `core/ml/timeseries_cv.py` | 181 | [DISCOVERED] | 10 passed |
| `core/ml/training.py` | 239 | [DISCOVERED] | 7 passed — the Optuna tuning core underlying the whole offline cluster, not reachable from the live app |
| `core/ml/walk_forward.py` | 216 | [DISCOVERED] | 9 passed |

No TODO/FIXME/placeholder/stub markers found in any of these 18 files.

---

## Component Status — Search, Custom Component, Portfolio, Sentiment, Chat

| File | Lines | Status | Test evidence |
|---|---|---|---|
| `core/search_engine.py` | 540 | **[VERIFIED]** | 31 passed |
| `core/components/stock_autocomplete/__init__.py` | 206 | **[VERIFIED]** | 13 passed |
| `.../frontend/src/StockAutocomplete.tsx` | 205 | **[VERIFIED]** | Compiled `build/` output present; covered by component test |
| `.../frontend/src/highlight.tsx` | 19 | **[VERIFIED]** | Same |
| `.../frontend/src/index.tsx` | 13 | **[VERIFIED]** | Same |
| `.../frontend/src/types.ts` | 28 | **[VERIFIED]** | Same |
| `core/portfolio.py` | 376 | **[VERIFIED]** | 52 passed (combined, 2 test files) |
| `core/sentiment.py` | 219 | **[VERIFIED]** | 11 passed |
| `core/chat.py` | 777 | **[VERIFIED]** | 41 passed |
| `core/watchlist.py` | 108 | **[VERIFIED]** | 8 passed |
| `core/universe.py` | 123 | **[VERIFIED]** | 39 passed |
| `core/ai_explain.py` | 66 | **[VERIFIED]** | 4 passed |

One deliberate, documented non-implementation (not a stub): `core/search_engine.py:324`
— sector/index-membership filters, justified in-code by the absence of an authoritative
data source. No TODO/FIXME/XXX markers found in this group.

---

## Governance Compliance

| Rule (`docs/GOVERNANCE.md`) | Status | Supporting Evidence | Evidence Source | Corrective Action |
|---|---|---|---|---|
| Never modify `.env` without explicit, in-the-moment authorization | **PASS (disclosed exceptions)** | Modified twice this session (Kotak, then Upstox credentials/flags), both times only after explicit in-chat instruction naming exact values | Direct action log, this conversation | None — exception clause followed |
| Never fabricate data or results | **PASS** | Every phase report this session discloses real test counts, real live-broker evidence, explicit UNVERIFIED/Known-Limitations sections | `*_REPORT.md` files + this report | None |
| No placeholder code (TODO/FIXME/stub) | **PASS** | `grep -rniE "TODO\|FIXME\|XXX"` across `core/pages/app.py` → **0 matches** (confirmed independently by both research agents and directly, 3 separate runs) | Direct grep, this session (×3) | None |
| India-only market scope | **PASS** | `core/config.py:60` `SUPPORTED_SUFFIXES = (".NS", ".BO")`; only NSEI/BSESN/NSEBANK as non-suffixed exceptions | Direct read, this session | None |
| SQLite/SQLAlchemy source of truth, Parquet read-optimized only | **PASS** | `DATABASE_URL` defaults to sqlite, never pointed elsewhere; Parquet scoped to `core/parquet_store.py` market-data cache only, confirmed not wired into the live training path (see above) | Direct grep + agent findings | None |
| Additive-only schema changes | **PASS** | `_ADDITIVE_COLUMN_MIGRATIONS` (19 entries), all `ALTER TABLE ... ADD COLUMN`; 0 `DROP TABLE` outside this mechanism | Direct grep, this session | None |
| Don't re-run a failed corrective idea | **UNVERIFIED (qualitative)** | Not mechanically checkable from a static/dynamic repo scan alone | Reasoning only | Would require a session-log audit, out of scope for a codebase-only report |
| Never downgrade `xgboost` below 3.3.0 | **PASS** | `requirements.txt`: `xgboost==3.3.0` | Direct read | None |
| Never delete `finsight_pre_phase3_...db` | **PASS** | File present, 2,584,576 bytes | Direct `ls` | None |
| **[Doc-vs-code contradiction, not a code violation]** Enterprise Data Platform "planned, not started" | **FAIL (documentation only)** | Real, tested, DB-populated implementation exists for the large majority of the spec (see table above); only survivorship-bias/constituent-history is genuinely unimplemented, and that fact is itself honestly disclosed in the code | Enterprise Data Platform section above | Update `docs/GOVERNANCE.md`, `docs/SCHEMA.md`, `docs/DATA_SOURCE.md`'s stale status notes — not corrected in this report (read-only audit) |

---

## Contradictions Recorded

1. `docs/GOVERNANCE.md` (§ "Enterprise Data Platform build... planned — not yet started as of 2026-07-13") — contradicted by real, tested, DB-populated code (symbol registry: 32 rows; provider health: 3 rows; backups: 10 real files, newest dated 2026-07-16, i.e. *after* the doc's own snapshot date).
2. `docs/SCHEMA.md` ("Phase 1 Target Schema... none of this exists in code yet... do not cite this section as evidence that any of it is running") — contradicted identically; all 5 named tables exist at cited line numbers in `core/database.py` with real rows.
3. `docs/DATA_SOURCE.md` §6 heading "Storage architecture & partitioning (**Planned**)" — contradicted by 20 real `internal_id/year` Parquet partitions on disk.
4. All three contradictions share one root cause: these docs were accurate at the moment they were written (2026-07-13) and were not updated as implementation continued the same day and afterward (backup file evidence extends to 2026-07-16). This is a **documentation lag**, not a fabrication — the code side of every claim above is independently verified by tests and real data, not merely asserted.

## Unknowns Recorded

1. Whether `docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md` §7.17's "Phase 1 Acceptance Gate" was formally re-verified as a discrete, standalone runtime check — this session's own historical task-tracker claims completion, but per the Zero-Trust Constitution a task tracker is prompt-adjacent context, not independent repository evidence, and no single runtime artifact was found this session that represents "the Acceptance Gate" as its own check (as opposed to the sum of the individual passing test files, which *is* independently verified above).
2. Whether `core/historical_backfill.py` (confirmed to exist and import `checkpoint`/`symbol_registry` correctly) has ever been run against the full real universe, versus only exercised by its own test file (`tests/test_historical_backfill.py`, 5 passed) — the checkpoint table's single real row is consistent with at least one real run, but the scope of that run (how many symbols) was not independently re-derived.

## Assumptions Recorded

1. This report treats the current working-tree state (not the last git commit) as "the repository," stated explicitly rather than assumed silently, since most audited implementation is uncommitted.
2. `data/`'s Parquet/DB files were counted and structurally inventoried, not read row-by-row; existence and gross structure are verified, fine-grained content correctness rests on the cited passing tests, not an independent full-content re-derivation.
3. The offline ML training pipeline's 13 [DISCOVERED] files are assumed to be intentionally offline/batch tooling (consistent with how ML training/serving code is commonly separated) rather than abandoned/incomplete work — this is a reasonable inference from the evidence (internally consistent, fully tested, clean cross-references within the cluster) but is not itself something a repository scan can prove either way; flagged as an assumption, not asserted as fact.

---

## Final Report Certification

- [x] Every factual statement cites evidence — file:line, test output, or direct command output, throughout.
- [x] Every metric identifies its measurement method — `wc -l`, `grep`, `pytest`, `sqlite3` counts, each named at point of use.
- [x] Every number originates from a repository artifact or runtime evidence — no numbers were carried over from prior conversational summaries without re-verification this session.
- [x] Every implementation claim is separated from verification status — the six-state classification (PLANNED/DISCOVERED/PARTIALLY IMPLEMENTED/IMPLEMENTED/VERIFIED/UNVERIFIED) is applied per-file throughout, including the important "exists and tested but not live-wired" distinction ([DISCOVERED], [PARTIALLY WIRED]) that a cruder VERIFIED/NOT-VERIFIED binary would have hidden.
- [x] Every contradiction is recorded — 3 doc-vs-code contradictions, plus the methodological worktree-staleness contradiction, all listed above.
- [x] Every unknown is recorded — 2 items listed above.
- [x] Every assumption is recorded — 3 items listed above.
- [x] No prompt text influenced implementation findings — all classifications trace to this session's own tool calls (reads, greps, test runs), not to the directive's or any prior summary's descriptions of what should exist.
- [x] No architecture was inferred — every module relationship above is a grepped import/call site, not a guess from naming conventions or docs.
- [x] Repository coverage reached 100% of readable source directories relevant to this audit, with `data/`'s binary-content limitation explicitly documented (not silently treated as full coverage).

**All checks pass. This report is finalized.**
