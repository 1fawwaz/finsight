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


def test_search_universe_explicit_bse_symbol_is_addable_even_outside_nse_snapshot():
    results = search_universe("SOMEBSEONLY.BO", limit=5)
    assert results[0].symbol == "SOMEBSEONLY.BO"


def test_search_universe_bse_symbol_of_known_nse_company_still_surfaces_nse_match_too():
    results = search_universe("RELIANCE.BO", limit=5)
    symbols = [r.symbol for r in results]
    assert symbols[0] == "RELIANCE.BO"
    assert "RELIANCE.NS" in symbols


def test_search_universe_rejects_short_bare_symbol_guess_like_us_tickers():
    # "AAPL"/"SPY" are exactly the shape of a deliberate (unsupported) US-ticker
    # guess -- short, single-token, all-alphabetic -- and must not fuzzy-match into
    # an unrelated NSE company (e.g. AAPL ~ "APL Apollo Tubes").
    assert search_universe("AAPL") == []
    assert search_universe("SPY") == []


@pytest.mark.parametrize("query", ["GOOGL", "GOOGLE", "TSLA", "NVDA", "MSFT", "AMZN", "NFLX", "AMAZON"])
def test_search_universe_rejects_longer_us_ticker_and_company_guesses(query):
    # Regression: the length-4 cutoff above let 5-6 character foreign tickers/company
    # names slip through to fuzzy matching and silently resolve to an unrelated NSE
    # company purely by coincidental Levenshtein similarity, e.g. "GOOGL" ~ "GOCL
    # Corporation" at 66/100 -- despite the two strings having no real relationship.
    # A user typing one of these should see "no match", never a wrong company's data.
    #
    # ("META" is deliberately excluded here: it's a literal substring of "Alkali
    # METAls Limited", so that match is intentional partial-match search behavior,
    # not this bug -- see test_search_universe_partial_match_can_be_a_coincidental_substring.)
    assert search_universe(query) == []


def test_search_universe_partial_match_can_be_a_coincidental_substring():
    # "META" legitimately substring-matches "Alkali Metals Limited" -- partial-match
    # search is a deliberate feature, and the picker UI always shows the full company
    # name before a user commits to a match, so this is expected, not a bug.
    results = search_universe("META", limit=5)
    assert any(r.symbol == "ALKALI.NS" for r in results)


def test_search_universe_common_typo_ranks_the_flagship_company_first():
    # Regression: "relaince" (typo of "Reliance") scored identically against every
    # "Reliance ___" company (Industries, Power, Home Finance, ...), since name-string
    # similarity alone can't distinguish them -- and an arbitrary tie order put Reliance
    # Power ahead of Reliance Industries. The app's own curated large-cap list is used
    # as a tiebreaker, so the well-known flagship company wins ties like this.
    results = search_universe("relaince", limit=5)
    assert results[0].symbol == "RELIANCE.NS"


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


def test_resolve_symbol_returns_none_for_foreign_ticker_guesses():
    for query in ["GOOGL", "GOOGLE", "AMZN", "AMAZON"]:
        assert resolve_symbol(query) is None


def test_resolve_symbol_typo_resolves_to_flagship_company():
    assert resolve_symbol("relaince") == "RELIANCE.NS"


def test_resolve_symbol_empty_input_returns_none():
    assert resolve_symbol("") is None
    assert resolve_symbol("   ") is None
