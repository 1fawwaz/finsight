# FinSight Component Library

Every shared UI primitive, what it replaces, and where it lives. Chart-level
components (`core/theme.py`'s `apply_dark_layout`) are out of scope here — this
covers DOM/Streamlit-widget-level primitives only.

## CSS-only primitives (`core/design.py`, applied globally via `inject_design_system()`)

| Primitive | Selector | Replaces |
|---|---|---|
| Card | `div[data-testid="stMetric"]` | Bare, unbordered `st.metric()` calls everywhere (ui-audit.md Finding 4) |
| Card (bordered container) | `div[data-testid="stVerticalBlockBorderWrapper"]` | AI Sentiment's one-off `st.container(border=True)` article cards — now share the same elevation language as every metric card instead of being visually isolated |
| Button states | `.stButton > button` (+hover/active/focus-visible) | Streamlit's flat default (no hover/press feedback) |
| Table frame | `div[data-testid="stDataFrame"]` | Hard-edged default table container |
| Divider | `hr` | Browser-default `<hr>`, now tied to the same gridline token as charts |
| Scrollbar | `::-webkit-scrollbar` | OS-default light scrollbar appearing inside dark-themed scroll areas |

## Python helpers (`core/ui_components.py`)

| Function | Signature | Purpose |
|---|---|---|
| `render_page_header` | `(title, subtitle=None, icon=None)` | One shared title+caption pattern instead of each page hand-rolling `st.title()` + `st.caption()` with inconsistent wording/spacing |
| `render_empty_state` | `(title, body, icon="📭")` | Consistent "nothing here yet" placeholder (empty watchlist, empty portfolio, no search matches) instead of ad hoc `st.info(...)` one-liners that varied in tone per page |
| `render_prediction_disclaimer` | `()` | *(pre-existing, unchanged)* Persistent AI/ML disclaimer banner |
| `render_ai_panel` | `(context_label, data, fallback_text, mode)` | *(pre-existing, unchanged)* Shared "AI Analysis" panel |
| `stock_search_and_pick` / `stock_picker` | — | *(pre-existing, unchanged)* Shared autocomplete widget |

## Python helpers (`core/design.py`)

| Function | Signature | Purpose |
|---|---|---|
| `inject_design_system` | `()` | Applies all CSS tokens/rules to the current page. Call once per page, right after `st.set_page_config`. |
| `section_header` | `(title, subtitle=None)` | A consistent mid-page section heading (`### title` + optional caption), replacing the mix of bare `st.subheader`/`st.caption` pairs scattered per page |

## Deliberately not built

- **A custom button component** — Streamlit's native `st.button` already has one
  consistent style app-wide (ui-audit.md: button style count = 1); CSS-only
  hover/press/focus states were sufficient, a full component would be a new
  dependency for zero behavioral gain.
- **A custom table/grid** — `st.dataframe` already provides sort, and adding a
  virtualized/custom grid would reintroduce exactly the kind of unjustified new
  architecture the project's repository-first governance rule (`docs/GOVERNANCE.md`)
  warns against, for a dataset size (max ~2,400 rows) that doesn't need it.
