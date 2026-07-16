# UI Technical Debt Register

Honest accounting of what this pass did **not** fix, and why -- so nothing is
silently left for a future session to rediscover from scratch.

## 1. `st.dataframe` currency columns remain Western-grouped

`st.column_config.NumberColumn(format="₹%.2f")` cannot express Indian digit
grouping (it's a static printf-style format string), while
`core.formatting.format_inr()` (the correct, Indian-grouped formatter for this
India-only app) is used everywhere else. Pre-formatting table columns as strings
to fix this would break native numeric sort (lexicographic string sort orders
"₹1,000" before "₹250"). **Deliberately left as a documented exception**
(`DESIGN_SYSTEM.md`'s "Currency formatting" section) rather than silently
inconsistent or falsely claimed fixed. A real fix would require either a custom
Streamlit column type (new dependency, out of scope) or giving up native sort.

## 2. `core/theme.py`'s dead light-mode tokens were not deleted

`INK_PRIMARY`, `INK_SECONDARY`, `INK_MUTED`, `GRIDLINE`, `STATUS_SERIOUS` have zero
live references (confirmed via grep, `ui-audit.md` Finding 2). Left in place rather
than deleted: removing them is a pure code-cleanup action with no visual effect,
and this pass prioritized shipping the (much higher-value) missing design-system
layer over unrelated dead-code removal. Flagged here so a future pass can delete
them with a one-line justification instead of rediscovering the "are these really
unused?" question from scratch.

## 3. No automated visual regression tooling was introduced

`VISUAL_REGRESSION_REPORT.md`'s verification was manual (live screenshots, by
hand, after each change) rather than an automated pixel-diff tool (Percy,
Playwright snapshot testing, etc.). No such tool exists in this repository today,
and introducing one is a real, standing dependency/CI decision that's out of scope
for a single UI pass to make unilaterally (consistent with this project's
repository-first, justify-new-dependencies governance rule).

## 4. Chart accessibility (screen readers) is unaddressed

Plotly charts (candlesticks, heatmaps, gauges, treemaps) have no text-alternative
or underlying-data-table fallback for screen-reader users. This predates this
session's work and is a real, standing gap -- not something a CSS/design-token
pass can fix without adding a parallel accessible-data-table component per chart
(a meaningfully larger, separate project).

## 5. Mobile/touch verification could not be performed

Documented in both `RESPONSIVE_REPORT.md` and `ACCESSIBILITY_REPORT.md`: the
available browser automation could not force a narrow viewport or simulate touch
events in this environment. Framework-level responsive CSS was confirmed present
(Streamlit's own `768px`/`640px`/`576px` breakpoints), but no visual mobile
screenshot or touch-interaction verification exists for this session's changes.

## 6. The autocomplete component's CSS is a separately-built, hand-synced file

`core/components/stock_autocomplete/frontend/src/styles.css` now uses the *same
hex values* as `core/design.py` (fixed this phase, see `COMPONENT_LIBRARY.md`/
`ACCESSIBILITY_REPORT.md`), but they're two files, manually kept in sync by
comment references to each other -- not a single shared token source Python and
the Vite/React build both import. A true single-source-of-truth (e.g. generating
the component's CSS variables from the same token file at build time) would
require build-tooling changes to the component's Vite config, judged out of scope
for this pass given the component's few, now-aligned color values.

## What was NOT left as debt (verified, not assumed)

- Full regression suite (648 tests) passes after every change in this phase --
  re-run and confirmed multiple times, not run once and assumed stable.
- No new dependencies were added (`inject_design_system()` uses only
  `st.markdown(unsafe_allow_html=True)`, a built-in Streamlit escape hatch).
- No fabricated metrics or invented "before" screenshots -- every "before" claim in
  `ui-audit.md` has a file:line citation; every "after" claim in
  `VISUAL_REGRESSION_REPORT.md` has a live-session screenshot behind it.
