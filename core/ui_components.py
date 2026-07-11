"""The single shared "search for an NSE stock" widget, used everywhere a stock can be
picked or added (Market Overview watchlist, Stock Analysis, Portfolio, AI Sentiment,
ML Signals) so the experience is identical across the app and the user never has to
type a `.NS`/`.BO` suffix anywhere.
"""

from __future__ import annotations

import streamlit as st

from core.universe import UniverseEntry, search_universe

SEARCH_PLACEHOLDER = "e.g. Reliance, TCS, INFY, HDFC Bank..."


def display_symbol(symbol: str) -> str:
    """Symbol without its exchange suffix, for compact display (RELIANCE, not RELIANCE.NS)."""
    return symbol.removesuffix(".NS").removesuffix(".BO")


def _match_labels(matches: list[UniverseEntry]) -> list[str]:
    return [f"{display_symbol(m.symbol)} — {m.name}" for m in matches]


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
    return matches[labels.index(choice)]


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
        else:
            st.caption(f"No NSE-listed company found matching '{query}'. Keeping {display_symbol(st.session_state[key])}.")

    return st.session_state[key]
