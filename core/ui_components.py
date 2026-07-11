"""The single shared "search for an NSE stock" widget, used everywhere a stock can be
picked or added (Market Overview watchlist, Stock Analysis, Portfolio, AI Sentiment,
ML Signals) so the experience is identical across the app and the user never has to
type a `.NS`/`.BO` suffix anywhere.
"""

from __future__ import annotations

import streamlit as st

from core.ai_explain import generate_ai_panel
from core.explain import Explanation, PREDICTION_DISCLAIMER
from core.universe import UniverseEntry, display_symbol, search_universe

SEARCH_PLACEHOLDER = "e.g. Reliance, TCS, INFY, HDFC Bank..."

MOOD_ICON = {"good": "\U0001F7E2", "worried": "\U0001F534", "neutral": "⚪"}


def _match_labels(matches: list[UniverseEntry]) -> list[str]:
    return [f"{display_symbol(m.symbol)} — {m.name}" for m in matches]


RECENT_SEARCHES_KEY = "recent_searches"
RECENT_SEARCHES_MAX = 8


def _record_recent_search(symbol: str) -> None:
    """Track resolved symbols across the whole session (any page's search box) for the
    home dashboard's "Recent Searches" panel. Most-recent-first, deduped, capped."""
    recent = st.session_state.setdefault(RECENT_SEARCHES_KEY, [])
    if symbol in recent:
        recent.remove(symbol)
    recent.insert(0, symbol)
    del recent[RECENT_SEARCHES_MAX:]


def stock_search_and_pick(
    key: str,
    label: str = "Search for a stock",
    placeholder: str = SEARCH_PLACEHOLDER,
) -> UniverseEntry | None:
    """Live search-as-you-type over the full NSE universe; returns the entry the user
    selected, or None until they've typed something with at least one match.

    This is the one-shot "find a stock to add" building block (watchlist, portfolio
    holdings). For a persistent single-stock picker that a whole page revolves around,
    use `stock_picker` instead.
    """
    query = st.text_input(label, key=f"{key}_query", placeholder=placeholder)
    if not query or not query.strip():
        return None
    matches = search_universe(query, limit=8)
    if not matches:
        st.caption(f"No NSE-listed company found matching '{query}'.")
        return None
    labels = _match_labels(matches)
    choice = st.selectbox("Select the company", labels, key=f"{key}_choice", label_visibility="collapsed")
    picked = matches[labels.index(choice)]
    _record_recent_search(picked.symbol)
    return picked


def stock_picker(
    key: str,
    default_symbol: str,
    label: str = "Search for a stock",
    placeholder: str = SEARCH_PLACEHOLDER,
) -> str:
    """A persistent, page-level "which stock am I looking at" search box.

    Remembers the resolved symbol in `st.session_state[key]` across reruns (e.g. after
    clicking an action button on the same page), defaulting to `default_symbol` until
    the user searches for something else. Returns the canonical `.NS`/`.BO` symbol --
    callers never see or need to construct a suffix.
    """
    if key not in st.session_state:
        st.session_state[key] = default_symbol

    query = st.text_input(label, key=f"{key}_query", placeholder=placeholder)
    if query and query.strip():
        matches = search_universe(query, limit=8)
        if matches:
            labels = _match_labels(matches)
            choice = st.selectbox("Select the company", labels, key=f"{key}_choice")
            st.session_state[key] = matches[labels.index(choice)].symbol
            _record_recent_search(st.session_state[key])
        else:
            st.caption(f"No NSE-listed company found matching '{query}'. Keeping {display_symbol(st.session_state[key])}.")

    return st.session_state[key]


MODE_SIMPLE = "Simple"
MODE_PROFESSIONAL = "Professional"


def render_mode_toggle() -> str:
    """Sidebar Simple/Professional mode toggle, shared and persisted across every page
    for the whole browser session. Defaults to Simple Mode -- the spec's explicit bar
    is that a 10-year-old with no finance background can use this app confidently, so
    plain-language explanations are what a first-time visitor sees unless they opt into
    Professional Mode themselves.
    """
    if "mode" not in st.session_state:
        st.session_state["mode"] = MODE_SIMPLE
    with st.sidebar:
        st.radio(
            "Mode",
            options=[MODE_SIMPLE, MODE_PROFESSIONAL],
            key="mode",
            help="Simple Mode explains everything in plain language. Professional Mode shows full technical detail.",
        )
    return st.session_state["mode"]


def render_explanation(explanation: Explanation, mode: str) -> None:
    """Render one metric's explanation as a mood-colored caption, text chosen by mode."""
    text = explanation.simple if mode == MODE_SIMPLE else explanation.professional
    st.caption(f"{MOOD_ICON[explanation.mood]} {text}")


def render_prediction_disclaimer() -> None:
    """Persistent, unmissable disclaimer for any panel showing an AI/ML prediction."""
    st.warning(f"⚠️ {PREDICTION_DISCLAIMER}")


@st.cache_data(ttl=1800, show_spinner=False)
def _cached_ai_panel(context_label: str, data_key: tuple, fallback_text: str, mode: str) -> tuple[str, bool]:
    return generate_ai_panel(context_label, dict(data_key), fallback_text, mode)


def render_ai_panel(context_label: str, data: dict, fallback_text: str, mode: str) -> None:
    """The shared "AI Analysis" panel for every analytical page: a Gemini-narrated
    synthesis of that page's own already-computed numbers (never invented), with a
    rule-based fallback if Gemini is unavailable or fails.

    Cached per (context_label, data, mode) so switching tabs, toggling a checkbox, or
    any other same-page rerun doesn't re-call Gemini for numbers it already explained.
    """
    st.subheader("What the AI Thinks" if mode == MODE_SIMPLE else "AI Analysis")
    data_key = tuple(sorted(data.items()))
    text, used_gemini = _cached_ai_panel(context_label, data_key, fallback_text, mode)
    st.info(text)
    if not used_gemini and mode == MODE_PROFESSIONAL:
        st.caption("Rule-based summary -- Gemini unavailable or not configured.")
