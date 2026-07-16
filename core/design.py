"""FinSight design system: the single source of truth for typography, spacing, and
extended color roles, plus the CSS injection that applies them across every page.

Built on top of `core/theme.py` (chart colors) rather than replacing it -- charts
keep using `theme.py` directly since Plotly needs raw hex values, not CSS. This
module is for everything Streamlit renders as DOM: metrics, buttons, cards, tables,
headings. Every value here has a reason (see `ui-audit.md` for the inconsistencies
this replaces and `DESIGN_SYSTEM.md` for the rationale/contrast evidence).

Call `inject_design_system()` once near the top of every page (after
`st.set_page_config`). It only emits CSS -- no layout side effects -- so calling it
repeatedly across pages/reruns is safe and cheap.
"""

from __future__ import annotations

import streamlit as st

from core import theme

# --- Color roles -----------------------------------------------------------
# Base surfaces/text reuse theme.py's existing dark-mode values exactly (this is
# the app's real, already-shipped background) so charts and DOM never diverge.
BG = theme.DARK_BG
SURFACE = theme.DARK_CARD_BG
SURFACE_RAISED = "#1c212e"  # one step lighter than SURFACE, for hover/active states
BORDER = theme.DARK_GRIDLINE
TEXT_PRIMARY = theme.DARK_INK_PRIMARY
TEXT_MUTED = theme.DARK_INK_MUTED

# Streamlit's own primaryColor (#2a78d6) is 4.28:1 against BG -- passes WCAG AA for
# large text/UI components (>=3:1) but not small body text (needs 4.5:1). Used as-is
# for buttons/borders/focus rings (UI components, not text). For inline text links
# and info-emphasis, TEXT_LINK is a lightened step that clears 4.5:1 (measured 6.01:1).
ACCENT = theme.CATEGORICAL[0]
TEXT_LINK = "#5b93de"

SUCCESS = theme.STATUS_GOOD       # 5.63:1 vs BG -- passes AA
WARNING = theme.STATUS_WARNING    # 10.3:1 vs BG -- passes AA
DANGER = theme.STATUS_CRITICAL    # 3.93:1 vs BG -- UI-component/large-text only, not body text
INFO = TEXT_LINK

# --- Typography scale --------------------------------------------------
# Streamlit's default sizes are consistent by construction (same widget, same size,
# everywhere) but were never an explicit, named scale. Naming it makes intent
# legible and gives the CSS below something concrete to apply.
FONT_DISPLAY = "2rem"      # 32px / 700 -- page-level hero numbers (e.g. Portfolio Value)
FONT_H1 = "1.75rem"        # 28px / 700 -- st.title
FONT_H2 = "1.375rem"       # 22px / 600 -- st.header
FONT_H3 = "1.125rem"       # 18px / 600 -- st.subheader
FONT_BODY = "0.9375rem"    # 15px / 400 -- default text
FONT_SMALL = "0.8125rem"   # 13px / 400 -- st.caption
FONT_MICRO = "0.6875rem"   # 11px / 600, uppercase, tracked -- metric labels/eyebrows

# --- Spacing scale (8pt grid) -----------------------------------------------
SPACE_1 = "0.25rem"  # 4px
SPACE_2 = "0.5rem"   # 8px
SPACE_3 = "0.75rem"  # 12px
SPACE_4 = "1rem"     # 16px
SPACE_6 = "1.5rem"   # 24px
SPACE_8 = "2rem"     # 32px

RADIUS_SM = "6px"
RADIUS_MD = "10px"
RADIUS_LG = "14px"

SHADOW_CARD = "0 1px 2px rgba(0,0,0,0.24), 0 1px 1px rgba(0,0,0,0.16)"
SHADOW_CARD_HOVER = "0 4px 12px rgba(0,0,0,0.32), 0 2px 4px rgba(0,0,0,0.24)"

TRANSITION = "150ms cubic-bezier(0.4, 0, 0.2, 1)"

_CSS = f"""
<style>
:root {{
  --fs-bg: {BG};
  --fs-surface: {SURFACE};
  --fs-surface-raised: {SURFACE_RAISED};
  --fs-border: {BORDER};
  --fs-text: {TEXT_PRIMARY};
  --fs-text-muted: {TEXT_MUTED};
  --fs-accent: {ACCENT};
  --fs-link: {TEXT_LINK};
  --fs-success: {SUCCESS};
  --fs-warning: {WARNING};
  --fs-danger: {DANGER};
  --fs-radius-sm: {RADIUS_SM};
  --fs-radius-md: {RADIUS_MD};
  --fs-radius-lg: {RADIUS_LG};
  --fs-shadow-card: {SHADOW_CARD};
  --fs-shadow-card-hover: {SHADOW_CARD_HOVER};
  --fs-transition: {TRANSITION};
}}

/* Typography scale made explicit -- same visual sizes Streamlit already renders,
   now backed by named tokens instead of implicit browser/library defaults. */
h1 {{ font-size: {FONT_H1} !important; font-weight: 700 !important; letter-spacing: -0.01em; }}
h2 {{ font-size: {FONT_H2} !important; font-weight: 600 !important; }}
h3 {{ font-size: {FONT_H3} !important; font-weight: 600 !important; }}
p, li, label {{ font-size: {FONT_BODY}; }}

/* Metric "cards" -- this single rule turns every existing st.metric() call across
   the whole app (Home KPIs, Stock Analysis stats, Portfolio risk metrics, ML
   Signals prediction) into a real bordered/elevated card with zero page-code
   changes, directly closing ui-audit.md Finding 4 (no reusable card component). */
div[data-testid="stMetric"] {{
  background: var(--fs-surface);
  border: 1px solid var(--fs-border);
  border-radius: var(--fs-radius-md);
  padding: {SPACE_4} {SPACE_6};
  box-shadow: var(--fs-shadow-card);
  transition: box-shadow var(--fs-transition), transform var(--fs-transition), border-color var(--fs-transition);
}}
div[data-testid="stMetric"]:hover {{
  box-shadow: var(--fs-shadow-card-hover);
  border-color: rgba(42, 120, 214, 0.35);
  transform: translateY(-1px);
}}
div[data-testid="stMetricLabel"] {{
  font-size: {FONT_MICRO};
  font-weight: 600;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--fs-text-muted) !important;
}}
div[data-testid="stMetricValue"] {{
  font-size: {FONT_DISPLAY};
  font-weight: 700;
  color: var(--fs-text) !important;
}}

/* Buttons: real hover/press/focus states, matched to the same elevation language
   as cards, instead of Streamlit's flat default. */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
  border-radius: var(--fs-radius-sm) !important;
  transition: transform var(--fs-transition), box-shadow var(--fs-transition), filter var(--fs-transition);
}}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover {{
  transform: translateY(-1px);
  box-shadow: var(--fs-shadow-card);
  filter: brightness(1.08);
}}
.stButton > button:active, .stDownloadButton > button:active, .stFormSubmitButton > button:active {{
  transform: translateY(0);
  filter: brightness(0.96);
}}
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible {{
  outline: 2px solid var(--fs-accent);
  outline-offset: 2px;
}}

/* Bordered containers (st.container(border=True)) and expanders share the same
   card language as metrics, so the app's one hand-built "card" (AI Sentiment's
   article list) now matches every other card instead of being a one-off. */
div[data-testid="stVerticalBlockBorderWrapper"], div[data-testid="stExpander"] {{
  border-radius: var(--fs-radius-md) !important;
  transition: box-shadow var(--fs-transition), border-color var(--fs-transition);
}}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {{
  box-shadow: var(--fs-shadow-card);
}}

/* Tables: rounded, bordered frame instead of a hard-edged rectangle, consistent
   with the card radius used everywhere else. */
div[data-testid="stDataFrame"] {{
  border-radius: var(--fs-radius-md);
  overflow: hidden;
  border: 1px solid var(--fs-border);
}}

/* Section dividers: quieter than the browser-default <hr>, matching the gridline
   token already used in every chart via core/theme.py. */
hr {{
  border-color: var(--fs-border) !important;
  opacity: 1;
}}

/* Scrollbar: dark-mode-native instead of the OS-default light scrollbar that
   otherwise appears inside scrollable code blocks/dataframes. */
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-track {{ background: var(--fs-bg); }}
::-webkit-scrollbar-thumb {{ background: var(--fs-border); border-radius: 8px; }}
::-webkit-scrollbar-thumb:hover {{ background: var(--fs-text-muted); }}

/* Sidebar nav links: subtle hover affordance, tokenized radius. */
[data-testid="stSidebarNav"] a {{
  border-radius: var(--fs-radius-sm);
  transition: background-color var(--fs-transition);
}}
</style>
"""


def inject_design_system() -> None:
    """Apply the shared token CSS to the current page. Idempotent and layout-free --
    safe to call once near the top of every page, every rerun."""
    st.markdown(_CSS, unsafe_allow_html=True)


def section_header(title: str, subtitle: str | None = None) -> None:
    """A consistent section heading with an optional muted subtitle, replacing the
    ad hoc mix of st.subheader/st.caption pairs scattered across pages with one
    shared, spacing-correct primitive."""
    st.markdown(f"### {title}")
    if subtitle:
        st.caption(subtitle)
