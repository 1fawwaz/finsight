# Performance Report

Phase 4 of the Production Stabilization Directive. Every number below was
produced by running actual code on the live repository this session, not
estimated. Where a directive-mandated scale target could not be reached in this
environment, it is marked **Unverified** with the specific reason, per this
directive's own instruction not to extrapolate from small-scale numbers.

## Reproducibility contract (applies to every measurement below unless noted)

- **Environment**: Windows-11-10.0.26200-SP0, Python 3.12.10 (64-bit), single
  physical machine, no containerization/VM isolation, other processes (Chrome,
  IDE, this agent session) concurrently running -- i.e., not a dedicated,
  isolated benchmark box. This is disclosed because it means these numbers carry
  more noise than a clean CI runner would produce (see the startup-time variance
  below), not because the numbers are invalid.
- **Dataset**: the real `data/finsight.db` (27,662 price rows, 2,387-symbol NSE
  universe) unless a test explicitly created its own throwaway temp database
  (noted per-measurement).
- **Timestamp**: this session, 2026-07-14.
- Exact commands are inlined in each section below.

## App Startup

**Target: ≤2s, process invocation to fully operational state.**

Command: `streamlit run app.py --server.headless true`, timed from process
launch to the first `HTTP 200` response from `http://localhost:8501` (polled
every 100ms).

| Trial | Time |
|---|---|
| 1 | 3,785ms |
| 2 | 2,132ms |
| 3 | 3,360ms |

**Mean: 3,092ms. Result: budget NOT met** (3 of 3 trials exceeded 2,000ms; the
best trial still exceeded it). n=3 -- too small for a rigorous p95, reported as
the plain mean/range rather than overclaiming statistical precision.

**Root cause**: Streamlit's own framework bootstrap plus this app's import chain
(`pandas`, `SQLAlchemy`, `scikit-learn`, `catboost`, `xgboost`, `lightgbm`,
`optuna`, `shap`, `plotly` -- all imported somewhere on the startup path) is
inherently multi-second in Python; this is a known characteristic of the
scientific-Python import stack, not a regression introduced this session
(confirmed by a separate, narrower measurement: bare `import core.database;
init_db()` alone took ~1.6s, meaning well over half the total startup budget is
consumed before Streamlit's own server code even runs).

**Not fixed in this pass**: lazy-importing the heavy ML libraries (only needed by
the ML Signals/backtesting pages, not the Home dashboard) could reduce this, but
touches import structure across `core/ml_model.py` and its dependents broadly
enough that it's logged as debt for a dedicated pass rather than attempted under
this directive's Release Freeze caution against structural changes.

## Global Search / Autocomplete

**Target: ≤100ms.**

Command: 30 calls to `core.search_engine.search_stocks()` (10 distinct real
queries x 3 repetitions) against the real 2,387-symbol universe, after one
untimed warmup call (index build).

**Result: mean 16.67ms, p50 13.93ms, p95 32.14ms, p99 35.97ms, max 35.97ms.
Budget met with wide margin.** Consistent with this session's earlier
`SEARCH_QUALITY_REPORT.md` measurement (11.29ms mean at real scale), confirming
no regression.

## AI Prediction Latency

**Target: ≤500ms, full path (inference, caching, data prep).**

Command: 20 calls to `core.ml_model.predict_next_direction()` against
RELIANCE.NS's real 1,239-row price history, after one untimed warmup call.

**Result: mean 36.56ms, p50 36.58ms, p95 40.69ms, max 40.69ms. Budget met with
wide margin.** Scope note: this covers the ML direction-classifier inference
path itself, not the separate, optional Gemini-narrated "AI Analysis" panel
(`render_ai_panel`), which is independently cached (`@st.cache_data(ttl=1800)`)
and calls an external API on a cache miss -- a different, already-cached code
path not part of this specific target's stated scope ("prediction latency").

## Memory Stability

**Target: ~0 net growth over a 30-minute simulated session.**

Command: a single long-running Python process repeatedly called
`search_stocks()` (every iteration) and `predict_next_direction()` (every 5th
iteration) for 2,000 iterations, sampling process working-set size every 200
iterations via the Windows `GetProcessMemoryInfo` API (no new dependency --
`ctypes` is in the standard library).

**Result** (real wall time: 152.3s / ~2.5 minutes -- a scaled-down proxy for the
30-minute target, not the literal duration; disclosed honestly rather than
implied to be the full 30 minutes):

| Iteration | Elapsed | Working Set |
|---|---|---|
| 0 | 0.2s | 289.87 MB |
| 200 | 15.5s | 358.51 MB |
| 1,000 | 77.0s | 358.57 MB |
| 2,000 | 152.3s | 358.56 MB |

**Growth from iteration 200 to 2,000: +0.05 MB over 136.8s and 1,800 additional
iterations.** The jump between iteration 0 and 200 (289.87MB -> 358.51MB) is
one-time warmup allocation (first-use JIT/cache costs in pandas/scikit-learn
internals), not a leak -- the flat line from 200 onward is the actual signal.
**No memory leak detected** in the operations profiled. **Not fully verified**:
a literal 30-minute continuous run, and the Portfolio page's heavier operations
(Monte Carlo simulation, correlation matrix) were not included in this specific
profiling pass -- logged as a follow-up in the Technical Debt Register.

## N+1 Query Fix (found and fixed this phase — see `BUG_FIX_REPORT.md`)

`core.portfolio.list_holdings()` eager-loads its `ticker` relationship now
instead of lazy-loading one row at a time. Measured on a throwaway 5,000-row
temp-DB portfolio: **1,880ms before -> ~620ms after (3.0x improvement)**, and a
new regression test (`test_list_holdings_does_not_n_plus_one`) asserts the SQL
query *count* stays flat regardless of row count, so this can't silently regress.

## Scalability Gates — reported honestly per this directive's own instruction

| Target | Status | Detail |
|---|---|---|
| ≥10,000 active ticker symbols | **Unverified** | Real universe is 2,387 symbols (NSE-listed equities, a fixed real-world count -- there is no "more" real data to add). A synthetic 11,935-symbol universe (5x) was benchmarked in an earlier session's search-engine work (`SEARCH_QUALITY_REPORT.md`: 35.99ms mean at that scale) but not re-verified this session; not re-claimed here |
| ≥1,000,000 historical market records | **Unverified** | Real `data/finsight.db` holds 27,662 rows; generating 1M+ synthetic rows and re-validating every query path against them was not performed this session due to time constraints, not because it's infeasible -- flagged as a priority for the next cycle |
| ≥100 concurrent active user sessions | **Partially verified, with an important distinction** | 100 concurrent *in-process Python threads* writing via `get_session()` were tested live (`BUG_FIX_REPORT.md` Finding 4): 100/100 succeeded, zero database lock errors, confirming the DB layer itself serializes safely under thread-level concurrency. **Not verified**: 100 genuinely separate Streamlit server sessions/browser connections (real multi-user infrastructure, load-generation tooling, and multiple machines/processes were not available in this single-developer-machine environment) |
| Multi-thousand-row portfolios, long-lived sessions | **Partially verified** | A 5,000-row synthetic portfolio was created and its read/list path measured and fixed (see N+1 above). A genuinely long-lived (multi-hour) session was not simulated -- the 152.3s memory-stability run is the closest available evidence, showing no growth trend over that shorter window |

## UI Interaction Smoothness (60fps, chart pans)

**Unverified.** No browser frame-rate profiling tool (e.g., Chrome DevTools
Performance panel automation, `requestAnimationFrame` instrumentation) was
available via this session's browser-automation tooling. Plotly.js (which
renders every chart in this app) handles pan/zoom entirely client-side in the
browser, independent of the Python backend measured elsewhere in this report --
profiling it properly requires browser-side tooling this environment doesn't
expose. Not claimed as passing; not guessed at.

## Summary

| Metric | Budget | Measured | Status |
|---|---|---|---|
| App startup | ≤2s | 3,092ms mean (n=3) | **Not met** — root cause is the ML-library import chain, logged as debt |
| Global search | ≤100ms | 16.67ms mean, 32.14ms p95 | **Met** |
| AI prediction | ≤500ms | 36.56ms mean, 40.69ms p95 | **Met** |
| Memory stability | ~0 net growth / 30min | +0.05MB / 136.8s (2.5min proxy) | **Met** for the window tested |
| 60fps chart interaction | 0 dropped frames | — | **Unverified** — no tooling available |
| 10K symbols / 1M records / 100 sessions | — | — | **Unverified at true scale** — see table above for what was and wasn't tested |
