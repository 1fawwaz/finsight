# Portfolio Module — Root Cause Analysis & Fix Log

Live document. Source of truth for resuming this investigation without re-tracing.

## Dependency map (confirmed via repository inspection, not assumed)

```
pages/3_Portfolio.py (UI)
  -> core.ui_components.stock_search_and_pick (session state: "add_holding" autocomplete)
       -> core.components.stock_autocomplete.stock_autocomplete (the custom component, built this session)
  -> core.portfolio (service/calculation layer)
       add_holding / delete_holding / list_holdings / list_portfolios / create_portfolio
       portfolio_weights / sector_allocation / diversification_score / sharpe_ratio /
       max_drawdown / portfolio_daily_returns / portfolio_volatility / risk_level /
       correlation_matrix / monte_carlo_simulation
  -> core.database (ORM: Portfolio, Holding, Ticker) via core.database.get_session
  -> core.queries (get_price_history, get_multi_symbol_close, get_ticker_info) -- read path
  -> core.data_ingestion (ingest_ticker, get_or_create_ticker, upsert_prices) -- price/ticker creation
  -> st.cache_data-wrapped _load_history / _load_info (page-local caches, ttl=900s)
  -> plotly charts (allocation, sector allocation, risk gauge, cumulative return, correlation, Monte Carlo)
```

No duplicate Portfolio service, calculation engine, or repository layer exists anywhere
else in the repo (grep-confirmed: `core/portfolio.py` is the only module defining
`add_holding`/`delete_holding`/portfolio math; `pages/3_Portfolio.py` is the only UI
consumer).

## Problem classification (per-issue, before any fix)

| # | Symptom | Classification | Status |
|---|---|---|---|
| 1 | Add Position: holding never appears, no metric updates, no error | Session State / Integration | Investigating |
| 2 | (latent, found during investigation of #1) Stale widget-key cleanup after add | Session State (dead code) | Investigating |
| 3 | (latent, found during code inspection) Duplicate-symbol holdings silently collide in portfolio-level totals | Business Logic / Data Integrity | Investigating |
| 4 | "Edit Position" required by directive's Acceptance Gate, but no such feature exists in `core/portfolio.py` or the UI | Business Logic / UI (missing feature, not a regression) | Investigating |

## Investigation log

### Issue #1: Add Position appears to do nothing

**Hypothesis (from static code trace, `pages/3_Portfolio.py:95-109` + `core/components/stock_autocomplete/__init__.py`):**

`stock_search_and_pick` now calls the new `stock_autocomplete()` primitive (built
earlier this session), which is documented to return the selected `SearchResult`
"in the exact script run the selection happened -- None on every other run" (a
one-shot consume-then-clear design). Traced run-by-run:

- Run A (user clicks/Enters a result): `_render_autocomplete_fragment` detects the
  fresh `select` event, stores it in `st.session_state[selection_key]`, calls
  `st.rerun(scope="app")` — which aborts Run A immediately. `stock_autocomplete()`
  never reaches its own `return` statement in Run A.
- Run B (the forced rerun): `_component_func` returns the *same* select event
  (nothing changed client-side) → `event == state["last_event"]` → skipped as
  already-processed. `stock_autocomplete()` reads `st.session_state[selection_key]`
  (still set from Run A), returns it, **and immediately clears it**
  (`st.session_state[selection_key] = None`) so it isn't returned twice. This is the
  run where the "Add SYMBOL" button appears — matches what was directly observed in
  this session's own earlier browser verification of the autocomplete component.
- Run C (user clicks "Add SYMBOL"): a button click triggers a full script rerun.
  `stock_autocomplete()` runs again; the select event is still unchanged
  (`event == state["last_event"]`) so no reprocessing/rerun happens; it reads
  `st.session_state[selection_key]`, which is now `None` (cleared at the end of Run
  B) → **returns `None`**. `pages/3_Portfolio.py`'s guard
  `if add_clicked and match is not None:` is therefore `False` on the exact run
  where the user clicked "Add" — `add_holding()` is never called, no error is shown
  (there is no `else` branch), and every downstream symptom (holdings table,
  portfolio value, P&L, allocation, charts) is trivially explained by the fact
  nothing was ever inserted.

This fully explains every one of the user-reported symptoms for the Add flow, not
just some of them: no DB row → no holdings → no value/P&L change → no allocation
change → no chart change → and no error, because the code path is a silent no-op,
not an exception.

**Status: CONFIRMED and FIXED.** Live evidence (before fix): navigated to Portfolio,
created a portfolio, searched RELIANCE, selected it (button correctly showed "Add
RELIANCE"), entered Shares=10/Avg Cost=1300 via Tab between fields — the button
silently reverted to disabled "Add Holding" before any click. Clicking the disabled
button did nothing; scrolled down to confirm "No holdings yet. Add one above." with
no error shown anywhere -- exactly the reported symptom.

**Fix**: `core/components/stock_autocomplete/__init__.py` -- changed the selection
from a one-shot consumed-on-first-read value to a persistent value in per-key state
(`state["selected_result"]`), cleared only by a genuinely new query/clear/select
event on *that* search box, plus a new `reset_autocomplete(key)` / `core.ui_components
.reset_stock_search(key)` for callers to explicitly clear it after acting on a
selection. `pages/3_Portfolio.py` and (same shared-component bug, same fix)
`pages/1_Market_Overview.py`'s watchlist-add both call `reset_stock_search(...)`
right after a successful add.

**A second, narrower bug found during the fix**: `pages/3_Portfolio.py` never called
`core.database.init_db()` (unlike `pages/1_Market_Overview.py` and
`pages/7_Ask_FinSight_AI.py`, which do) -- Streamlit's `pages/` convention runs each
page as its own independent script, so navigating directly to `/Portfolio` in a tab
that hadn't already visited a page calling `init_db()` skipped the additive-column
migration for this session, causing an `OperationalError: no such column:
portfolios.updated_at` the moment the new `Portfolio.updated_at` column (added for
the Portfolio Management fix, see below) was queried. Fixed by adding the same
`init_db()` call `pages/3_Portfolio.py` was missing.

**Post-fix live evidence**: repeated the exact same reproduction (RELIANCE, 10
shares, ₹1300 avg cost, Tab between fields) -- the "Add RELIANCE" button correctly
stayed enabled and correctly labeled through the field edits. Clicking it: DB query
directly after confirms `Holding(id=1, portfolio_id=11, symbol=RELIANCE.NS,
shares=10.0, avg_cost=1300.0)` was actually inserted. UI updated with zero manual
refresh: holdings table (Market Value ₹12,969.00, Gain/Loss -0.24%), Allocation pie
(RELIANCE 100%), Risk Metrics (Sharpe 0.38, Max Drawdown -27.4%), Sector Allocation
(Energy) all rendered correctly on the very next render after the click. Search box
also correctly reset to empty (via the new `reset_stock_search` call) instead of
continuing to offer "Add RELIANCE" again.

### Issue #3: Duplicate-symbol holdings silently collide in portfolio-level totals

**Confirmed by code inspection** (not yet needed a live repro -- the bug is a
deterministic Python dict-comprehension behavior, not a runtime-dependent one):
`pages/3_Portfolio.py`'s old `shares_map = {h["symbol"]: h["shares"] for h in
holdings ...}` overwrites, not sums, when the same symbol appears in more than one
`Holding` row (a real, already-tested, intentional scenario --
`tests/test_portfolio_crud.py::test_add_holding_reuses_existing_ticker` explicitly
asserts adding the same symbol twice creates two separate rows, so this is existing,
deliberate design, not something to "fix" at the `add_holding` layer). Any symbol
bought via two separate Add actions would silently understate total portfolio value/
weights/allocation, using only the last-read lot's share count.

**Fix**: added `core.portfolio.aggregate_shares_by_symbol(holdings) -> dict[str,
float]` (sums shares per symbol) and switched `pages/3_Portfolio.py` to use it
instead of the raw dict comprehension. Does not touch `add_holding`'s existing,
tested multi-lot behavior at all -- purely fixes the downstream aggregation.

### Issue #4: "Edit Position" required by the Acceptance Gate but not implemented anywhere

**Confirmed by code inspection**: no `update_holding`/`edit_holding` function existed
in `core/portfolio.py`, and no edit UI existed in `pages/3_Portfolio.py` -- only Add
and Delete. This is a missing feature, not a regression.

**Fix**: added `core.portfolio.update_holding(holding_id, shares, avg_cost) -> bool`
(same not-found-is-a-noop convention as `delete_holding`) and a new "Edit a holding"
control in the page, mirroring the existing "Remove a holding" selectbox pattern
exactly (same UI idiom, no new architecture).

## Full browser verification (all steps, real evidence)

1. Add RELIANCE (10 shares, avg cost 1300): DB row created
   (`Holding(id=1, portfolio_id=11, RELIANCE.NS, 10.0, 1300.0)`); holdings table,
   Market Value (12,969.00), Gain/Loss (-0.24%), Allocation pie (100% RELIANCE),
   Sharpe (0.38), Max Drawdown (-27.4%), Sector Allocation (Energy) all rendered on
   the immediate next render, no manual refresh.
2. Add TCS (5 shares, avg cost 2000): both RELIANCE and TCS rows appeared with
   independent, correct values (TCS: Market Value 10,345.00, Gain/Loss 3.45%); DB
   confirmed 2 rows.
3. Edit TCS to 20 shares: DB row updated in place (`id=2` unchanged,
   `shares 5.0 -> 20.0`); table immediately showed TCS Market Value recalculated to
   41,380.00.
4. Delete RELIANCE: DB row removed (only TCS's row, id=2, remained); table
   immediately dropped RELIANCE.
5. Browser refresh (fresh navigation to `/Portfolio`, re-selecting "Core Holdings"
   since portfolio *selection* is UI session state, not persisted data): TCS 20
   shares / Market Value 41,380.00 still present -- confirms DB persistence across a
   refresh.
6. Full application restart (`Stop-Process` on the streamlit process, then a fresh
   `streamlit run`): direct DB query after restart confirms all 11 pre-existing
   portfolios and the TCS holding (`id=2, portfolio_id=11, TCS.NS, 20.0 shares`)
   survived -- `data/finsight.db` is a real on-disk SQLite file, not in-memory state.

Every functional requirement in the directive (holdings table, portfolio value,
today's/total gain-loss proxied by Market Value/Gain-Loss %% since no historical
purchase-date-vs-today tracking exists in this schema, allocation, sector allocation,
charts, risk metrics, position count via the holdings table row count) updates
immediately after add/edit/delete with zero manual refresh, confirmed with direct
DB queries as ground truth alongside the UI screenshots, not UI appearance alone.

## Portfolio Management additions (unique names, timestamps, Delete Portfolio)

- `core.database.Portfolio.updated_at` (additive column) -- bumped by
  `core.portfolio._touch_portfolio` whenever a holding under a portfolio is
  added/edited/deleted.
- `core.portfolio.create_portfolio` now rejects a case-insensitive duplicate name
  (`DuplicatePortfolioNameError`), surfaced as `st.error(...)` in the UI without
  creating a row.
- `core.portfolio.delete_portfolio(portfolio_id)` -- deletes the portfolio and
  cascades its holdings via the existing ORM `cascade="all, delete-orphan"`
  relationship (no manual holdings cleanup needed). No transaction/ledger table or
  portfolio-keyed cache exists in this schema to also clean up (documented in the
  function's own docstring, not silently skipped).
- UI: a "Delete this portfolio" expander with a warning naming the portfolio, a
  confirmation checkbox ("I understand -- permanently delete '<name>'"), and a
  "Delete Portfolio" button disabled until the checkbox is checked -- matching the
  directive's example dialog.

**Real-world verification, far beyond the directive's own scripted steps**: created
"Long Term" (RELIANCE 8 shares + TCS 3 shares) and "Swing Trading" (INFY 15 shares)
alongside the existing "Core Holdings" (TCS 20 shares) -- confirmed via direct DB
query that all three stayed fully independent (no cross-contamination). Tested the
Delete Portfolio confirmation UI on "Swing Trading" in the browser: expander showed
the exact warning/checkbox/button pattern from the directive; the button was
disabled until the checkbox was checked; clicking it deleted the portfolio and
cascaded its one holding, confirmed via DB query (portfolio and holding both gone,
`delete` logged).

Independently of my own testing, **the user manually exercised the same Delete
Portfolio feature 8 times in a row** (through a separate browser tab, on real
pre-existing portfolios including ones from before this session) as part of doing
their own data cleanup. Checked the server log and the database directly afterward:
every single delete cascaded its holdings correctly, left zero orphaned `Holding`
rows, and zero portfolios or holdings remain, while the shared `Ticker` reference
table (24 rows, correctly *not* portfolio-scoped, so correctly untouched) was left
alone. This is strong additional evidence the feature is robust under real,
repeated, human-driven use -- not just my own scripted test sequence.

## Task list disposition

- Demo/test data cleanup: **done by the user's own action**, verified after the
  fact (0 portfolios, 0 holdings, 24 tickers remaining -- a clean slate). No
  further action needed; there was nothing left needing "positive identification"
  since nothing remains at all.

## Files/functions to be touched (impact analysis, before editing)

- `core/components/stock_autocomplete/__init__.py::stock_autocomplete` — the
  selection-consumption logic. **Depended on by**: `core/ui_components.py`
  (`stock_search_and_pick`, `stock_picker`) — both call sites. **Tests covering it**:
  `tests/test_stock_autocomplete_component.py` (13 tests, none currently exercise the
  multi-run consumption sequence — a gap this investigation will close).
  **Pages affected by a fix here**: every page using either wrapper (Home, Market
  Overview, Portfolio, Stock Analysis, AI Sentiment, ML Signals) — a fix must not
  regress the already-verified behavior on any of them.
- `pages/3_Portfolio.py` — the dead widget-key cleanup (lines 107-108) referencing
  session-state keys (`add_holding_query`, `add_holding_choice`) that no longer exist
  under the new component's key scheme.

(Remaining sections filled in as the investigation proceeds.)
