"""Tests for core.search_engine: the single consolidated stock-search implementation.

core.universe.search_universe's own regression suite (tests/test_universe.py) already
covers ranking/typo/BSE/resolve_symbol behavior end-to-end through the thin wrapper --
these tests instead exercise the parts of the new public surface that wrapper doesn't
touch: normalize_query, the index lifecycle (build/get/refresh/add_symbol), filters,
personalization boosts, matched_substring highlighting, and duplicate prevention.
"""

import time

import pytest

import core.search_engine as se
from core.search_engine import SearchFilters, normalize_query, search_stocks


@pytest.fixture(autouse=True)
def _reset_index():
    """Each test gets a fresh index and leaves none behind for the next test --
    add_symbol/refresh_index tests mutate the module-level cache directly."""
    se._index = None
    yield
    se._index = None


# --- normalize_query -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  tcs  ", "TCS"),
        ("tcs.ns", "TCS"),
        ("tcs.bo", "TCS"),
        ("M&M", "M M"),
        ("l&t", "L T"),
        ("Dr. Reddy's", "DR REDDY'S"),
        ("Tata-Steel", "TATA STEEL"),
        ("hdfc   bank", "HDFC BANK"),
    ],
)
def test_normalize_query(raw, expected):
    assert normalize_query(raw) == expected


# --- ranking tiers ---------------------------------------------------------------------


def test_exact_ticker_ranks_above_everything_else():
    results = search_stocks("TCS", limit=10)
    assert results[0].entry.symbol == "TCS.NS"
    assert results[0].tier_label == "exact_ticker"


def test_exact_name_beats_prefix_matches():
    # "Infosys" (not "Infosys Limited") is the exact match: indexed names are
    # compared after _clean_name strips generic corporate suffixes, so the boilerplate
    # "Limited" is never part of the comparison on either side.
    results = search_stocks("Infosys", limit=5)
    assert results[0].entry.symbol == "INFY.NS"
    assert results[0].tier_label == "exact_name"


def test_name_prefix_ranks_above_ticker_contains():
    # "tata" is a name prefix for many Tata group companies but not an exact/prefix
    # ticker for most of them -- prefix-of-name results should dominate the top of the
    # list, not an unrelated ticker that merely contains "TATA" as a substring.
    results = search_stocks("tata", limit=15)
    top_tiers = {r.tier_label for r in results[:5]}
    assert top_tiers <= {"exact_ticker", "exact_name", "name_starts_with", "ticker_starts_with"}


def test_fuzzy_tier_only_reached_when_nothing_else_matches():
    results = search_stocks("relaince", limit=5)
    assert any(r.tier_label == "fuzzy" for r in results)
    assert results[0].entry.symbol == "RELIANCE.NS"


def test_results_contain_no_duplicate_symbols():
    results = search_stocks("a", limit=50)
    symbols = [r.entry.symbol for r in results]
    assert len(symbols) == len(set(symbols))


def test_limit_is_respected():
    results = search_stocks("a", limit=3)
    assert len(results) <= 3


def test_empty_query_returns_nothing():
    assert search_stocks("") == []
    assert search_stocks("   ") == []


# --- filters -----------------------------------------------------------------------


def test_exchange_filter_nse_only_returns_ns_symbols():
    results = search_stocks("tata", filters=SearchFilters(exchange="NSE"), limit=15)
    assert results
    assert all(r.entry.symbol.endswith(".NS") for r in results)


def test_exchange_filter_bse_excludes_unsupplemented_nse_only_universe():
    # The bundled universe is NSE-only; a BSE filter against a name with no explicit
    # .BO entry in the index correctly yields nothing, rather than silently ignoring
    # the filter and returning NSE matches anyway.
    results = search_stocks("reliance", filters=SearchFilters(exchange="BSE"), limit=5)
    assert results == []


# --- explicit suffix branch (regression: this used to infinite-recurse) --------------


def test_explicit_bse_symbol_outside_snapshot_is_returned_without_recursion_error():
    results = search_stocks("SOMEBSEONLYCO.BO", limit=1)
    assert len(results) == 1
    assert results[0].entry.symbol == "SOMEBSEONLYCO.BO"
    assert results[0].tier_label == "explicit_suffix"


def test_explicit_suffix_with_room_for_more_appends_remaining_matches():
    results = search_stocks("RELIANCE.BO", limit=5)
    symbols = [r.entry.symbol for r in results]
    assert symbols[0] == "RELIANCE.BO"
    assert "RELIANCE.NS" in symbols
    assert symbols.count("RELIANCE.BO") == 1  # not duplicated by the recursive remainder


# --- matched_substring (UI highlighting) --------------------------------------------


def test_matched_substring_for_exact_ticker_is_the_ticker_itself():
    # "tcs" matches the ticker exactly (exact_ticker outranks exact_name), so the
    # highlighted substring comes from the (uppercase) symbol, not the company name.
    results = search_stocks("tcs", limit=1)
    assert results[0].tier_label == "exact_ticker"
    assert results[0].matched_substring.upper() == "TCS"


def test_matched_substring_preserves_original_casing_from_the_name():
    # A full-name query that doesn't also equal the ticker reaches the name tier, so
    # the highlighted text comes from the original-cased company name.
    results = search_stocks("Tata Consultancy Services", limit=1)
    assert results[0].entry.symbol == "TCS.NS"
    assert results[0].tier_label == "exact_name"
    assert results[0].matched_substring == "Tata Consultancy Services"


def test_matched_substring_handles_ampersand_tickers():
    results = search_stocks("are&m", limit=1)
    assert results[0].entry.symbol == "ARE&M.NS"
    assert results[0].matched_substring is not None
    assert results[0].matched_substring.replace("&", "").upper() == "AREM"


def test_matched_substring_is_none_for_fuzzy_tier():
    results = search_stocks("relaince", limit=5)
    fuzzy = [r for r in results if r.tier_label == "fuzzy"]
    assert fuzzy and all(r.matched_substring is None for r in fuzzy)


# --- personalization boosts ----------------------------------------------------------


def test_context_boost_reorders_within_a_tied_tier():
    # Many companies' names start with "T" -- a wide tie at name_starts_with, broken
    # (absent a boost) by the is_default proxy/alphabetical order. Boosting a symbol
    # further down that tie should override both and move it to the very top: the
    # sort key orders boost ahead of is_default, so personalization always wins ties.
    query = "t"
    baseline = search_stocks(query, limit=20)
    baseline_symbols = [r.entry.symbol for r in baseline]
    assert len(baseline_symbols) > 1
    target = baseline_symbols[1]

    boosted = search_stocks(query, limit=20, context={"watchlist_symbols": {target}})
    assert boosted[0].entry.symbol == target


def test_context_none_applies_no_boost():
    a = search_stocks("t", limit=20, context=None)
    b = search_stocks("t", limit=20)
    assert [r.entry.symbol for r in a] == [r.entry.symbol for r in b]


# --- index lifecycle -----------------------------------------------------------------


def test_get_index_is_cached_across_calls():
    first = se.get_index()
    second = se.get_index()
    assert first is second


def test_refresh_index_builds_a_new_object():
    first = se.get_index()
    second = se.refresh_index()
    assert first is not second
    assert second.built_at >= first.built_at


def test_add_symbol_is_incremental_not_a_full_rebuild():
    index_before = se.get_index()
    built_at_before = index_before.built_at
    row_count_before = len(index_before.frame)

    se.add_symbol("NEWCO.NS", "New Test Company Limited")

    index_after = se.get_index()
    assert index_after.built_at == built_at_before  # same object, no rebuild timestamp change
    assert len(index_after.frame) == row_count_before + 1

    results = search_stocks("New Test Company", limit=5)
    assert any(r.entry.symbol == "NEWCO.NS" for r in results)
    results_by_ticker = search_stocks("NEWCO", limit=5)
    assert any(r.entry.symbol == "NEWCO.NS" for r in results_by_ticker)


def test_add_symbol_is_idempotent_for_an_already_indexed_symbol():
    se.get_index()
    row_count = len(se.get_index().frame)
    se.add_symbol("TCS.NS", "Tata Consultancy Services")
    assert len(se.get_index().frame) == row_count


def test_add_symbol_before_any_index_exists_builds_one():
    assert se._index is None
    se.add_symbol("ANOTHERNEW.NS", "Another New Company")
    assert se._index is not None


# --- performance sanity (loose bound, not a strict benchmark) ------------------------


def test_search_completes_comfortably_within_the_100ms_budget():
    se.get_index()  # warm the cache first -- only steady-state latency is bounded here
    start = time.perf_counter()
    for query in ["tcs", "reliance", "hdfc bank", "relaince", "tat", "m&m"]:
        search_stocks(query, limit=10)
    elapsed_per_query = (time.perf_counter() - start) / 6
    assert elapsed_per_query < 0.1
