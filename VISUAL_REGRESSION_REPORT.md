# Visual Regression Report

## Method

Every page was loaded live in Chrome against the running app (real database,
real user data -- the "fawwz" portfolio with RELIANCE/TMCV/ADANIPOWER holdings,
the real watchlist, real cached price history) after each design-system change,
not a synthetic/mocked fixture. Screenshots were taken and inspected after each
page's changes landed (Phase 3), and again in this final pass. The full
automated test suite (648 tests) was re-run after every batch of changes and
once more at the end of this phase -- **648/648 passing, zero regressions**,
confirmed immediately above this report was written.

## Per-page result

| Page | Verified | Notes |
|---|---|---|
| Home (`app.py`) | ✅ | Index metrics, Portfolio Value, Watchlist table render as cards; empty-state primitive confirmed (Recent Searches, before any search) |
| Market Overview | ✅ | Watchlist table, filters, page header render correctly with real 12-symbol watchlist |
| Stock Analysis | ✅ | Candlestick/volume/RSI/MACD subplot renders correctly with RELIANCE's real price history; overlay checkboxes still functional |
| Portfolio | ✅ | Both the empty state (`<none>` selected) and the loaded state (real "fawwz" portfolio, 3 holdings) verified; Risk Metrics render as cards; Add Holding button-row alignment fix confirmed (no more visible gap from the removed `st.write("")` hack) |
| AI Sentiment | ✅ | Analyze button/search-box alignment fix confirmed; article cards render with the shared card elevation/hover language instead of being visually isolated |
| ML Signals | ✅ | Prediction card, warning banner, backtest metrics all render correctly |
| Ask FinSight AI | ✅ | Example-query button grid, chat input, disclaimer banner all render correctly |
| About | ✅ | Static content page, header/typography consistent with the rest of the app |

## Regressions found

**None.** Every page that was manually exercised (Portfolio's add/edit/delete
flows, Market Overview's watchlist add, Home's Quick Search, ML Signals'
backtest) continued to work identically to before the design-system changes --
the CSS injected by `inject_design_system()` only adds visual styling to
existing DOM nodes (`data-testid` selectors Streamlit itself renders) and never
removes, hides, or repositions functional elements.

## What "before/after" means for this report

No automated pixel-diffing tool (e.g. Percy, Playwright screenshot comparison)
is part of this repository's toolchain, and none was introduced for this task
(consistent with the project's repository-first / no-unjustified-new-dependency
rule). "Before" evidence is `ui-audit.md`'s Phase 1 findings (exact file:line
citations of the prior state); "after" evidence is the live screenshots
described above, taken by hand at each phase. This is a manual, evidence-backed
visual regression pass, not an automated one -- stated plainly rather than
implying tooling that isn't there.
