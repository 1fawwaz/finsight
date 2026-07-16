# Portfolio Fix Report

Date: 2026-07-14. Condensed pointer + summary for this final audit; full detail and
raw evidence lives in `PORTFOLIO_COMPLETION_REPORT.md` and
`PORTFOLIO_IMPLEMENTATION_LOG.md` (both already produced earlier this session and
re-verified, not re-derived, for this audit).

## Root cause (confirmed with trace evidence)

The Portfolio module's "Add Position does nothing, no error shown" bug traced to
`core.components.stock_autocomplete.stock_autocomplete()` returning the user's
selection as a **one-shot** value — consumed and cleared the first time it was read.
The instant the user interacted with any *other* widget on the page (the Shares
field, the Avg Cost field, or the Add button itself), the selection had already been
cleared on a prior run, so `if add_clicked and match is not None:` silently
evaluated false. No exception, no `else` branch — hence no error.

**Reproduced live, twice** (before and after the fix): selected RELIANCE, filled
Shares/Avg Cost, observed the "Add RELIANCE" button silently revert to disabled
"Add Holding" before ever being clicked (before-fix); after the fix, the same
sequence kept the button correctly enabled and the click inserted the DB row.

## Fix

Changed the selection to a persistent value in per-key session state, cleared only
by a genuinely new query/clear/select event on that specific search box — plus a
`reset_autocomplete()`/`reset_stock_search()` escape hatch for callers to explicitly
clear it after acting on a selection. Fixed once, at the shared component level,
which also fixed the identical latent bug in Market Overview's "Add to Watchlist"
flow.

## Additional issues found and fixed during the same investigation

1. **Duplicate-symbol aggregation bug** — `pages/3_Portfolio.py`'s
   `{symbol: shares}` dict comprehension silently dropped all but the last lot for
   any symbol bought in more than one Add action. Fixed via
   `core.portfolio.aggregate_shares_by_symbol()`.
2. **Missing Edit Position feature** — required by the acceptance gate but not
   implemented anywhere. Added `core.portfolio.update_holding()` + a matching UI
   control.
3. **Missing `init_db()` call** on `pages/3_Portfolio.py` (unlike its two sibling
   pages) — caused an `OperationalError` once the new `updated_at` column existed.
   Fixed by adding the same call the other pages already had.
4. **Portfolio Management gaps** — no unique-name enforcement, no `updated_at`
   timestamp, no Delete Portfolio capability. All three added, each with a
   confirmation-gated UI for the destructive one.

## Verification evidence (condensed; full detail in the source reports)

- 46 dedicated tests (`test_portfolio.py` + `test_portfolio_crud.py`), all passing,
  covering add/edit/delete, duplicate names, duplicate-symbol aggregation, empty
  portfolio, fractional/zero/negative shares, cascade-delete, and timestamp bumping.
- Full regression suite: 648/648 passing (this audit's own re-run, see
  `TEST_REPORT.md`).
- Live browser verification: add RELIANCE → all metrics updated same-render; add
  TCS → both holdings shown independently; edit TCS to 20 shares → recalculated
  correctly; delete RELIANCE → removed correctly; browser refresh → data persisted;
  full application restart → data persisted (re-confirmed *again* in this final
  audit, independently, against the real, currently-live "fawwz" portfolio — not a
  repeat of the same test data).
- **Independent real-world validation**: the user exercised the Delete Portfolio
  feature 8 times on their own, on real pre-existing portfolios, through a separate
  browser tab, entirely outside my own test script. Every one of those 8 deletions
  cascaded its holdings correctly with zero orphaned records — the strongest
  available evidence this feature is robust under real, unscripted use.

## Status

**Complete.** One explicitly flagged gap carried over from the original fix: formal
profiler-based performance numbers (`<200ms`/`<100ms` targets) were never measured
with a profiler — recorded as Unverified rather than claimed, consistent with this
audit's own `PERFORMANCE_REGRESSION_REPORT.md`.
