# Global Search & Autocomplete Report

Date: 2026-07-14. Condensed pointer + summary for this final audit; full detail and
raw evidence lives in `SEARCH_ENGINE_COMPLETION_REPORT.md`,
`AUTOCOMPLETE_MIGRATION_REPORT.md`, `SEARCH_QUALITY_REPORT.md`, and
`docs/SEARCH_ENGINE.md` (all produced earlier this session, re-verified rather than
re-derived for this audit).

## Search Engine consolidation

Every stock-search implementation in the repository was consolidated behind one
function, `core.search_engine.search_stocks()`. `core.universe.search_universe` is
now a thin, behavior-preserving wrapper (39 pre-existing tests pass unchanged
through it). 10-tier ranking algorithm, `&`-aware normalization, exchange filtering,
and real personalization boosts (recent searches, watchlist, portfolio — sourced
from existing data, no new tracking infrastructure) are all implemented and tested
(31 dedicated tests in `test_search_engine.py`).

**Quality evidence** (from `SEARCH_QUALITY_REPORT.md`, re-verified still accurate):
100% top-1 precision, 0% duplicate rate across 29 queries in 10 categories; 11.29ms
mean / 17.58ms max latency at real 2,387-symbol scale, 35.99ms mean / 67.64ms max at
synthetic 11,935-symbol (5x target) scale — comfortably inside the 100ms budget,
which is why no trie/inverted-index was built (justified by measurement, documented
in `docs/SEARCH_ENGINE.md` §7).

## Autocomplete component

A custom bidirectional Streamlit Component (React/TypeScript frontend, compiled via
Vite) delivers genuinely live (no-Enter), debounced (175ms), keyboard-navigable
(Arrow/Enter/Tab/Escape), ARIA-compliant autocomplete — the one platform capability
native Streamlit widgets cannot provide (confirmed directly: `st.text_input` only
reruns on Enter/blur). Both `stock_search_and_pick` and `stock_picker` (the two
call-site contracts used by every page) route through this one shared component.

**A real regression was found and fixed during this same work** (see
`PORTFOLIO_FIX_REPORT.md` for the full trace): the component's rerun originally used
plain `st.rerun()`, which re-rendered the *entire* page per keystroke and
intermittently stole DOM focus. Fixed with `@st.fragment` + `st.rerun(scope="fragment")`
for typing, keeping `scope="app"` only for the final selection — re-verified live,
4 consecutive arrow-key presses correctly moved the highlight with zero page
navigation.

## Re-verification performed for this audit (not merely re-cited)

- Live search test this session: typed "infosys" on the Home page — correct top
  ranking (INFY), substring highlighting, real Watchlist badge, zero Enter
  presses required (see `RC_VALIDATION_REPORT.md`).
- Live search test on Market Overview's watchlist-add box: typed "sun pharma",
  selected SUNPHARMA, added and removed it successfully via the real UI.
- Search latency re-measured fresh this session: 22.93ms mean across 5 live
  queries — consistent with, not regressed from, the previously reported baseline
  (see `PERFORMANCE_REGRESSION_REPORT.md`).
- Repository re-scanned for orphaned search implementations
  (`grep -rln "def search_stocks\|def search_universe\|class SearchIndex"`) —
  confirmed still exactly `core/search_engine.py` + `core/universe.py`, no drift
  since the original consolidation.

## Status

**Complete.** Two explicitly documented, non-blocking gaps carried over from the
original work: Edge/touch verification was never performed (no Edge automation tool
or touch-event simulator available in this environment — Chrome-only verification
is what's actually been done, stated plainly rather than inferred); sector/Nifty
index-membership filters remain unimplemented because no authoritative constituent
dataset exists anywhere in this repository (the same external-data blocker as Phase
1 Steps 6/7/9 — see `FINAL_IMPLEMENTATION_REPORT.md` §22 Known Blockers).
