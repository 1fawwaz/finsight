"""Tests for core.universe: NSE company/ticker search and symbol resolution."""

import pytest

from core.universe import load_universe, resolve_symbol, search_universe


def test_load_universe_has_thousands_of_entries():
    universe = load_universe()
    assert len(universe) > 2000
    assert {"symbol", "name", "series"} <= set(universe.columns)
    assert (universe["symbol"].str.endswith(".NS") | universe["symbol"].isin(["^NSEI", "^BSESN"])).all()


@pytest.mark.parametrize(
    "query, expected_symbol",
    [
        ("reliance", "RELIANCE.NS"),
        ("RELIANCE", "RELIANCE.NS"),
        ("TCS", "TCS.NS"),
        ("tcs", "TCS.NS"),
        ("infosys", "INFY.NS"),
        ("wipro", "WIPRO.NS"),
        ("hdfc bank", "HDFCBANK.NS"),
    ],
)
def test_search_universe_finds_expected_top_match(query, expected_symbol):
    results = search_universe(query, limit=5)
    assert results, f"no results for {query!r}"
    assert results[0].symbol == expected_symbol


def test_search_universe_partial_prefix_returns_multiple_matches():
    results = search_universe("tat", limit=10)
    symbols = {r.symbol for r in results}
    assert len(results) >= 3
    assert any(s.startswith("TATA") for s in symbols)


def test_search_universe_partial_prefix_inf_finds_infosys():
    results = search_universe("inf", limit=10)
    assert any(r.symbol == "INFY.NS" for r in results)


def test_search_universe_typo_still_finds_closest_match_via_fuzzy_fallback():
    results = search_universe("relaince", limit=5)
    symbols = {r.symbol for r in results}
    assert "RELIANCE.NS" in symbols


def test_search_universe_multiword_typo_finds_closest_match():
    results = search_universe("hdfc bnk", limit=5)
    assert results[0].symbol == "HDFCBANK.NS"


def test_search_universe_rejects_short_bare_symbol_guess_like_us_tickers():
    # "AAPL"/"SPY" are exactly the shape of a deliberate (unsupported) US-ticker
    # guess -- short, single-token, all-alphabetic -- and must not fuzzy-match into
    # an unrelated NSE company (e.g. AAPL ~ "APL Apollo Tubes").
    assert search_universe("AAPL") == []
    assert search_universe("SPY") == []


def test_search_universe_empty_query_returns_nothing():
    assert search_universe("") == []
    assert search_universe("   ") == []


def test_search_universe_results_have_real_names_not_index_labels():
    results = search_universe("relaince", limit=5)
    for r in results:
        assert not r.name.isdigit()
        assert len(r.name) > 2


@pytest.mark.parametrize(
    "user_text, expected",
    [
        ("reliance", "RELIANCE.NS"),
        ("Reliance", "RELIANCE.NS"),
        ("TCS", "TCS.NS"),
        ("tcs.ns", "TCS.NS"),
        ("WIPRO.NS", "WIPRO.NS"),
        ("infosys", "INFY.NS"),
        ("nifty 50", "^NSEI"),
        ("sensex", "^BSESN"),
    ],
)
def test_resolve_symbol_never_requires_user_to_type_suffix(user_text, expected):
    assert resolve_symbol(user_text) == expected


def test_resolve_symbol_returns_none_for_garbage_input():
    assert resolve_symbol("zzzznotarealcompanyzzzz123") is None


def test_resolve_symbol_empty_input_returns_none():
    assert resolve_symbol("") is None
    assert resolve_symbol("   ") is None
