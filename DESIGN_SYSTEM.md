# FinSight Design System

Source of truth: `core/design.py` (DOM tokens + CSS injection) and `core/theme.py`
(Plotly chart colors, unchanged, referenced by `design.py` rather than duplicated).
This document explains the *why* behind each token; the code is the source of the
actual values.

## Why a design system, not a rewrite

Per `ui-audit.md`, FinSight had no accumulated multi-style debt (one button style,
one chart language, one nav pattern already) — the gap was an *absent* intentional
layer (typography scale, spacing rhythm, card primitive, verified color roles) on
top of a consistent-by-luck baseline. `core/design.py` adds that layer as a single
CSS injection (`inject_design_system()`), called once per page, targeting
Streamlit's own stable `data-testid` DOM hooks — no new frontend dependency, no
markup duplication, no per-page copy-pasted `<style>` blocks.

## Color roles

| Token | Value | Source | Contrast vs bg (#0e1117) | Usage |
|---|---|---|---|---|
| `BG` | `#0e1117` | `theme.DARK_BG` | — | Page background |
| `SURFACE` | `#161a23` | `theme.DARK_CARD_BG` | — | Card/metric background |
| `SURFACE_RAISED` | `#1c212e` | new | — | Hover state one step lighter than SURFACE |
| `BORDER` | `#262b38` | `theme.DARK_GRIDLINE` | — | Card/table borders, dividers |
| `TEXT_PRIMARY` | `#e8e9ed` | `theme.DARK_INK_PRIMARY` | 15.58:1 | Body text, headings |
| `TEXT_MUTED` | `#7d8290` | `theme.DARK_INK_MUTED` | 4.92:1 | Captions, metric labels |
| `ACCENT` | `#2a78d6` | `theme.CATEGORICAL[0]` / config.toml primaryColor | 4.28:1 | Buttons, focus rings, chart primary series |
| `TEXT_LINK` | `#5b93de` | new, lightened ACCENT | 6.01:1 | Inline links/emphasis text (ACCENT itself fails AA for body text at 4.28:1, so text usage gets this lightened step) |
| `SUCCESS` | `#0ca30c` | `theme.STATUS_GOOD` | 5.63:1 | Positive deltas, confirmations |
| `WARNING` | `#fab219` | `theme.STATUS_WARNING` | 10.3:1 | Caution states |
| `DANGER` | `#d03b3b` | `theme.STATUS_CRITICAL` | 3.93:1 | Destructive actions, errors (UI-component/large-text threshold only — see note) |

All contrast ratios computed via the WCAG relative-luminance formula against the
page background, not eyeballed. **Note on `DANGER`:** 3.93:1 clears the 3:1 AA
threshold for large text (≥18pt/24px) and UI components (borders, icons) but not
small body text (needs 4.5:1) — used here only for button backgrounds/borders/large
warning icons, never for small-print danger text, so no page uses it below AA.

`theme.py`'s `INK_PRIMARY`/`INK_SECONDARY`/`INK_MUTED`/`GRIDLINE` (light-mode
leftovers, ui-audit.md Finding 2) and `STATUS_SERIOUS` are intentionally **not**
promoted into `design.py` — they have zero live usage and pulling them in would
just re-import dead code into a new file.

## Typography scale

| Token | Size | Weight | Maps to |
|---|---|---|---|
| `FONT_DISPLAY` | 32px | 700 | Portfolio Value / hero numbers |
| `FONT_H1` | 28px | 700 | `st.title` |
| `FONT_H2` | 22px | 600 | `st.header` |
| `FONT_H3` | 18px | 600 | `st.subheader` |
| `FONT_BODY` | 15px | 400 | Body text, labels |
| `FONT_SMALL` | 13px | 400 | `st.caption` |
| `FONT_MICRO` | 11px | 600, uppercase, tracked | Metric eyebrow labels |

These match the sizes Streamlit already rendered by default (verified in
`ui-audit.md` — one consistent, undocumented scale). Naming them doesn't change
what the user sees at baseline; it gives every future change one scale to reference
instead of ad hoc per-element sizing.

## Spacing (8pt grid)

`SPACE_1`..`SPACE_8` = 4/8/12/16/24/32px, used inside `core/design.py`'s own CSS
(card padding, gaps) rather than as a general Streamlit layout primitive — Streamlit
itself controls block-level vertical rhythm (`st.columns`, `st.divider`), which is
out of CSS's reach without fighting the framework. Where a real spacing bug existed
(`pages/4_AI_Sentiment.py`'s `st.write("")` ×2 alignment hack), it's fixed directly
with `st.columns(vertical_alignment="bottom")` (native to Streamlit 1.33+, this repo
pins 1.38.0) instead of a CSS workaround.

## Currency formatting (Finding 1 resolution)

`core.formatting.format_inr()` (Indian digit grouping — correct for an India-only
app) is now the single formatter for every **non-tabular** rupee value: `st.metric`
calls (unchanged, already used it) and the two `core/chat.py`/`core/explain.py`
raw f-string call sites that had drifted onto Western grouping (`f"₹{v:,.2f}"`) —
fixed to call `format_inr()` directly.

**`st.dataframe` currency columns remain on `st.column_config.NumberColumn(format="₹%.2f")`
(Western grouping) — a deliberate, documented exception, not an oversight.**
Streamlit's `NumberColumn` format parameter is a static printf-style string; it
cannot express Indian digit grouping, and the column's *numeric* dtype is what
gives users click-to-sort in the table UI. Pre-formatting the column as a string to
get Indian grouping would silently break numeric sort (e.g. "₹1,000" would sort
before "₹250" lexicographically) — a worse regression than the grouping mismatch.
This tradeoff is stated here explicitly per the directive's own instruction not to
paper over a gap with false consistency.

## Component primitives

See `COMPONENT_LIBRARY.md` for the full catalogue. Headline addition:
`div[data-testid="stMetric"]` is now styled as a real elevated card
(border, radius, shadow, hover lift) — this single CSS rule turns every existing
`st.metric()` call across every page into a card with zero page-code changes,
directly resolving `ui-audit.md` Finding 4 (no reusable card component existed).

## How to use this system on a page

```python
from core.design import inject_design_system

st.set_page_config(...)
inject_design_system()
```

Call it once, right after `set_page_config`. It only emits `<style>` — no layout
side effects — so it's safe on every page, every rerun.
