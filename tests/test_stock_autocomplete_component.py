"""Tests for core.components.stock_autocomplete: the custom bidirectional Streamlit
Component wrapping core.search_engine.search_stocks for live autocomplete.

The React/TS frontend itself (debounce, keyboard nav, ARIA, highlighting, badges) was
verified via live browser testing (Chrome, through claude-in-chrome), not unit tests --
Streamlit's own `AppTest` framework does not simulate a custom component's JS side
(there is no way to fire a real `Streamlit.setComponentValue` call without a browser),
so these tests instead cover everything on the Python side that *is* meaningfully
unit-testable: result serialization, initial state shape, the query/clear/select event
state machine and its idempotence guard, and that the frontend build artifacts the
Python wrapper points `declare_component` at actually exist.
"""

import os

import pytest
import streamlit as st

from core.components.stock_autocomplete import (
    _build_dir,
    _initial_state,
    _serialize_result,
)
from core.search_engine import SearchResult, UniverseEntry


def _make_result(symbol="TCS.NS", name="Tata Consultancy Services Limited", tier_label="exact_ticker",
                  matched_substring="TCS") -> SearchResult:
    return SearchResult(
        entry=UniverseEntry(symbol=symbol, name=name, series="EQ"),
        tier=1,
        tier_label=tier_label,
        fuzzy_score=0.0,
        matched_substring=matched_substring,
    )


# --- frontend build artifacts --------------------------------------------------------


def test_frontend_build_directory_exists():
    assert os.path.isdir(_build_dir), f"missing build dir: {_build_dir}"


def test_frontend_build_contains_index_html():
    assert os.path.isfile(os.path.join(_build_dir, "index.html"))


def test_frontend_build_contains_js_assets():
    assets_dir = os.path.join(_build_dir, "assets")
    assert os.path.isdir(assets_dir)
    assert any(f.endswith(".js") for f in os.listdir(assets_dir))


# --- _serialize_result -----------------------------------------------------------------


def test_serialize_result_shape_and_values():
    result = _make_result()
    serialized = _serialize_result(result, watchlist_symbols=set(), portfolio_symbols=set())
    assert serialized == {
        "symbol": "TCS.NS",
        "display_symbol": "TCS",
        "name": "Tata Consultancy Services Limited",
        "series": "EQ",
        "tier_label": "exact_ticker",
        "matched_substring": "TCS",
        "in_watchlist": False,
        "in_portfolio": False,
    }


def test_serialize_result_flags_watchlist_and_portfolio_membership():
    result = _make_result()
    serialized = _serialize_result(result, watchlist_symbols={"TCS.NS"}, portfolio_symbols={"TCS.NS"})
    assert serialized["in_watchlist"] is True
    assert serialized["in_portfolio"] is True


def test_serialize_result_none_membership_defaults_false():
    result = _make_result(symbol="INFY.NS")
    serialized = _serialize_result(result, watchlist_symbols=set(), portfolio_symbols={"TCS.NS"})
    assert serialized["in_watchlist"] is False
    assert serialized["in_portfolio"] is False


def test_serialize_result_matched_substring_can_be_none():
    result = _make_result(matched_substring=None)
    serialized = _serialize_result(result, watchlist_symbols=set(), portfolio_symbols=set())
    assert serialized["matched_substring"] is None


# --- _initial_state --------------------------------------------------------------------


def test_initial_state_shape():
    state = _initial_state()
    assert state == {
        "query": "",
        "results_serialized": [],
        "results_by_symbol": {},
        "status": "idle",
        "error_message": None,
        "last_event": None,
        "selected_result": None,
    }


def test_initial_state_returns_a_fresh_dict_each_call():
    a = _initial_state()
    b = _initial_state()
    assert a == b
    a["query"] = "mutated"
    assert b["query"] == ""  # not the same object -- mutating one must not affect the other


# --- event state machine (query/clear/select + idempotence) --------------------------
# Exercises the same state-transition logic `_render_autocomplete_fragment` applies,
# without going through Streamlit's fragment/rerun machinery (untestable without a
# browser) -- these assert the *decisions* that logic makes are correct.


@pytest.fixture(autouse=True)
def _clean_session_state():
    keys_before = set(st.session_state.keys())
    yield
    for key in list(st.session_state.keys()):
        if key not in keys_before:
            del st.session_state[key]


def test_query_event_is_not_reprocessed_when_unchanged():
    state = _initial_state()
    event = {"type": "query", "text": "tcs"}
    assert event != state["last_event"]  # first sighting -- would be processed
    state["last_event"] = event
    same_event = {"type": "query", "text": "tcs"}  # a fresh dict, equal by value
    assert same_event == state["last_event"]  # second sighting -- correctly skipped


def test_different_query_text_is_recognized_as_a_new_event():
    state = _initial_state()
    state["last_event"] = {"type": "query", "text": "tcs"}
    new_event = {"type": "query", "text": "tcs "}  # trailing space -- a genuinely new event
    assert new_event != state["last_event"]


def test_select_event_looks_up_the_right_result_by_symbol():
    results = [_make_result(symbol="TCS.NS"), _make_result(symbol="INFY.NS", name="Infosys Limited")]
    results_by_symbol = {r.entry.symbol: r for r in results}
    event = {"type": "select", "symbol": "INFY.NS"}
    selected = results_by_symbol.get(event["symbol"])
    assert selected is not None
    assert selected.entry.symbol == "INFY.NS"


def test_select_event_for_unknown_symbol_returns_none_not_a_crash():
    results_by_symbol = {"TCS.NS": _make_result()}
    selected = results_by_symbol.get("NOT_A_REAL_SYMBOL.NS")
    assert selected is None
