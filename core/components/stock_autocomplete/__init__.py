"""The single reusable stock-search autocomplete widget -- a custom bidirectional
Streamlit Component (React/TS frontend under `frontend/`), backed exclusively by
`core.search_engine.search_stocks`. No ranking/matching/index logic lives here or in
the frontend; this module only serializes requests/results across the Python<->JS
boundary and manages the widget's per-key session state.

Bidirectional protocol, in brief (see the frontend's StockAutocomplete.tsx for the
client half): the JS side debounces keystrokes client-side (150-200ms) and calls
`Streamlit.setComponentValue({"type": "query", "text": ...})`. Any interaction with a
widget inside an `@st.fragment`-decorated function auto-triggers a *fragment-scoped*
rerun by default -- exactly what a "query"/"clear" event wants: only this widget's own
container re-renders with fresh results, the rest of the page never repaints. This
also fixes a real bug found during manual browser verification: an earlier version
called plain `st.rerun()` (app-scoped) to push updated `results` back into the
component after computing them, which re-ran and reconciled the *entire* page on every
keystroke -- and that full-page reconciliation was intermittently stealing DOM focus
away from the component's input (observed as arrow keys landing on the sidebar page
list instead of the dropdown). Scoping the rerun to the fragment keeps the rest of the
page's DOM untouched, so focus never leaves the input.

A `select` event is the one case that deliberately breaks out of fragment scope: the
selected stock needs to affect the *surrounding* page (add a holding, switch a chart),
so it stashes the result in session state and forces `st.rerun(scope="app")` to make
sure the calling code re-executes and can act on it.

The selection is a *persistent* value in per-key state (`state["selected_result"]`),
not a one-shot value consumed on first read. An earlier version cleared it the first
time `stock_autocomplete()` returned it non-None, on the theory that a caller only
needs to see a selection "once." That broke every "search, then fill in other fields,
then click a separate Add button" call site (Portfolio's Add Holding, Market
Overview's Add to Watchlist): each of those *other* widgets (a number_input, a
button) triggers its own full-script rerun before the user ever clicks Add, and on
that rerun the one-shot value had already been cleared -- so the Add button either
rendered disabled or its click was silently dropped, with no error shown. Confirmed
live in-browser (see PORTFOLIO_IMPLEMENTATION_LOG.md): after selecting RELIANCE and
merely tabbing between the Shares/Avg Cost fields, the "Add RELIANCE" button reverted
to a disabled "Add Holding" before it was ever clicked. The selection now behaves
like any other Streamlit widget's return value: stable across unrelated reruns,
replaced only by a genuinely new interaction with *this* search box (a new query, a
new selection, or an explicit clear), and resettable on demand via
`reset_autocomplete(key)` once a caller has actually acted on it.
"""

from __future__ import annotations

import os
import time

import streamlit as st
import streamlit.components.v1 as components

from core.config import get_logger
from core.search_engine import SearchFilters, SearchResult, get_index_health, search_stocks
from core.universe import display_symbol

logger = get_logger(__name__)

_COMPONENT_NAME = "stock_autocomplete"
_DEBOUNCE_MS = 175

_build_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "build")
_component_func = components.declare_component(_COMPONENT_NAME, path=_build_dir)


def _serialize_result(result: SearchResult, watchlist_symbols: set[str], portfolio_symbols: set[str]) -> dict:
    symbol = result.entry.symbol
    return {
        "symbol": symbol,
        "display_symbol": display_symbol(symbol),
        "name": result.entry.name,
        "series": result.entry.series,
        "tier_label": result.tier_label,
        "matched_substring": result.matched_substring,
        "in_watchlist": symbol in watchlist_symbols,
        "in_portfolio": symbol in portfolio_symbols,
    }


def _initial_state() -> dict:
    return {
        "query": "",
        "results_serialized": [],
        "results_by_symbol": {},
        "status": "idle",
        "error_message": None,
        "last_event": None,
        "selected_result": None,
    }


@st.fragment
def _render_autocomplete_fragment(
    key: str,
    state_key: str,
    label: str,
    placeholder: str,
    filters: SearchFilters | None,
    context: dict | None,
    limit: int,
) -> None:
    """The actual widget render + event handling, scoped as its own fragment so
    typing never triggers a full-page rerun (see module docstring). Writes a fresh
    selection into `state["selected_result"]` (persistent -- see module docstring)
    and force-reruns at app scope when one happens, since that's the one event the
    surrounding page must react to.
    """
    state = st.session_state[state_key]
    watchlist_symbols = (context or {}).get("watchlist_symbols") or set()
    portfolio_symbols = (context or {}).get("portfolio_symbols") or set()

    event = _component_func(
        label=label,
        placeholder=placeholder,
        query=state["query"],
        results=state["results_serialized"],
        status=state["status"],
        error_message=state["error_message"],
        disabled=False,
        debounce_ms=_DEBOUNCE_MS,
        key=key,
        default=None,
    )

    if event is None or event == state["last_event"]:
        return  # nothing new since the last time we processed an event

    state["last_event"] = event
    event_type = event.get("type")

    if event_type == "clear":
        state.update(
            query="", results_serialized=[], results_by_symbol={}, status="idle",
            error_message=None, selected_result=None,
        )
        st.rerun(scope="fragment")

    elif event_type == "query":
        query_text = event.get("text", "")
        logger.info("search_request query_length=%d key=%s", len(query_text), key)
        start = time.perf_counter()
        try:
            results = search_stocks(query_text, filters=filters, limit=limit, context=context)
            duration_ms = (time.perf_counter() - start) * 1000
            health = get_index_health()
            logger.info(
                "search_completed key=%s duration_ms=%.2f result_count=%d cache=%s",
                key, duration_ms, len(results), health["cache_status"],
            )
            state["query"] = query_text
            state["results_by_symbol"] = {r.entry.symbol: r for r in results}
            state["results_serialized"] = [
                _serialize_result(r, watchlist_symbols, portfolio_symbols) for r in results
            ]
            state["status"] = "success" if results else "empty"
            state["error_message"] = None
        except Exception as exc:  # search must never crash the page -- surface it in the widget instead
            logger.warning("search_error key=%s error=%s", key, exc)
            state["query"] = query_text
            state["results_serialized"] = []
            state["results_by_symbol"] = {}
            health = get_index_health()
            state["status"] = "index_unavailable" if not health["build_ok"] else "error"
            state["error_message"] = None if state["status"] == "index_unavailable" else str(exc)
        state["selected_result"] = None  # a new query supersedes any prior pick
        st.rerun(scope="fragment")

    elif event_type == "select":
        symbol = event.get("symbol")
        state["selected_result"] = state["results_by_symbol"].get(symbol)
        st.rerun(scope="app")


def stock_autocomplete(
    key: str,
    label: str = "Search for a stock",
    placeholder: str = "e.g. Reliance, TCS, INFY, HDFC Bank...",
    filters: SearchFilters | None = None,
    context: dict | None = None,
    limit: int = 20,
) -> SearchResult | None:
    """The single low-level autocomplete primitive every stock-search surface in the
    app is built on (`core.ui_components` wraps it for the two existing call-site
    contracts). Returns the currently-selected `SearchResult`, or `None` if nothing
    is selected yet -- stable across reruns caused by *other* widgets on the page
    (see module docstring), not a one-shot value. Cleared automatically when the user
    types a new query or clears the box; cleared on demand via
    `reset_autocomplete(key)` once a caller has acted on the selection.
    """
    state_key = f"_stock_autocomplete_state_{key}"
    st.session_state.setdefault(state_key, _initial_state())

    _render_autocomplete_fragment(key, state_key, label, placeholder, filters, context, limit)

    return st.session_state[state_key]["selected_result"]


def reset_autocomplete(key: str) -> None:
    """Clear a stock_autocomplete widget back to its empty/no-selection state.

    Call this after successfully acting on a selection (e.g. right after
    `add_holding(...)` succeeds) so the search box returns to empty instead of
    continuing to offer the same already-used pick. `key` must be the same key
    originally passed to `stock_autocomplete`/`stock_search_and_pick`.
    """
    state_key = f"_stock_autocomplete_state_{key}"
    st.session_state[state_key] = _initial_state()
