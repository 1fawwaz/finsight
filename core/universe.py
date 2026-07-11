"""The full NSE-listed equity universe, for name/ticker search and symbol resolution.

Backed by a bundled snapshot of NSE's public equity list (`core/data/nse_equity_list.csv`,
sourced from nsearchives.nseindia.com/content/equities/EQUITY_L.csv), so every valid
NSE-listed company is searchable and addable -- not just the small default watchlist.
Users never need to type the `.NS` suffix: this module resolves plain company names or
bare symbols to the correct Yahoo Finance ticker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from core.config import BENCHMARKS, is_supported_symbol, get_logger

logger = get_logger(__name__)

_UNIVERSE_CSV = Path(__file__).resolve().parent / "data" / "nse_equity_list.csv"

_CORP_SUFFIX_RE = re.compile(r"\b(limited|ltd|company|corporation|corp)\b\.?", re.IGNORECASE)
_FUZZY_SCORE_THRESHOLD = 65

_BENCHMARK_ENTRIES = [
    {"symbol": symbol, "name": display_name, "series": "INDEX"}
    for display_name, symbol in BENCHMARKS.items()
]


@dataclass(frozen=True)
class UniverseEntry:
    """A single searchable NSE listing."""

    symbol: str
    name: str
    series: str


def display_symbol(symbol: str) -> str:
    """Symbol without its exchange suffix, for compact display (RELIANCE, not RELIANCE.NS)."""
    return symbol.removesuffix(".NS").removesuffix(".BO")


def _clean_name(name: str) -> str:
    """Strip generic corporate suffixes ("Limited", "Ltd", ...) so fuzzy scoring
    compares the distinctive part of a company name rather than boilerplate."""
    return re.sub(r"\s+", " ", _CORP_SUFFIX_RE.sub("", name)).strip()


@lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    """Load the bundled NSE equity list into a DataFrame, indexed for search.

    Columns: symbol (with .NS suffix), name, series. Cached in-process since the
    underlying CSV is static reference data, not something that changes per request.
    """
    df = pd.read_csv(_UNIVERSE_CSV)
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"SYMBOL": "symbol", "NAME OF COMPANY": "name", "SERIES": "series"})
    df["symbol"] = df["symbol"].str.strip()
    df["name"] = df["name"].str.strip()
    df["series"] = df["series"].str.strip()
    df["symbol"] = df["symbol"] + ".NS"
    df = df[["symbol", "name", "series"]].drop_duplicates(subset="symbol")

    benchmarks_df = pd.DataFrame(_BENCHMARK_ENTRIES)
    universe = pd.concat([benchmarks_df, df], ignore_index=True)
    universe["name_clean"] = universe["name"].map(_clean_name)
    universe["name_tokens"] = universe["name_clean"].str.upper().str.split()
    return universe


def _best_token_match_score(query_tokens: list[str], name_tokens: list[str]) -> float:
    """Average, over each query word, of its best fuzzy match against any word in the
    candidate company name. Scoring per-word (rather than whole-string) keeps a typo in
    one word ("relaince") from getting diluted or outscored by generic industry words
    ("Insurance", "Finance", "Bank") shared across many unrelated companies."""
    if not query_tokens or not name_tokens:
        return 0.0
    return sum(max(fuzz.ratio(qt, nt) for nt in name_tokens) for qt in query_tokens) / len(query_tokens)


def search_universe(query: str, limit: int = 8) -> list[UniverseEntry]:
    """Search the NSE universe by company name or ticker (partial, case-insensitive, fuzzy).

    Ranks exact/prefix symbol matches highest, then name prefix matches, then fuzzy
    matches on name -- so typing "tat" surfaces every Tata group company, "inf" surfaces
    Infosys, and "reliance" or "RELIANCE" both find RELIANCE.NS.
    """
    query = query.strip()
    if not query:
        return []
    universe = load_universe()
    query_upper = query.upper()
    bare_query = query_upper.removesuffix(".NS").removesuffix(".BO")

    # An explicit, well-formed BSE symbol (e.g. "SOMECO.BO") isn't in the bundled
    # NSE-only snapshot, so it can't be found by the matching below -- surface it
    # directly as a synthetic top result instead, so BSE tickers stay addable even
    # without name search.
    if query_upper.endswith(".BO") and is_supported_symbol(query_upper) and query_upper not in set(universe["symbol"]):
        bse_entry = [UniverseEntry(symbol=query_upper, name=f"{bare_query} (BSE)", series="BO")]
        remaining = search_universe(bare_query, limit=limit - 1) if limit > 1 else []
        return bse_entry + [r for r in remaining if r.symbol != query_upper]

    symbol_bare = universe["symbol"].str.removesuffix(".NS")
    exact = universe[symbol_bare == bare_query]
    prefix_symbol = universe[symbol_bare.str.startswith(bare_query) & ~universe.index.isin(exact.index)]
    prefix_name = universe[
        universe["name"].str.upper().str.startswith(query_upper)
        & ~universe.index.isin(exact.index)
        & ~universe.index.isin(prefix_symbol.index)
    ]
    substring_name = universe[
        universe["name"].str.upper().str.contains(query_upper, regex=False)
        & ~universe.index.isin(exact.index)
        & ~universe.index.isin(prefix_symbol.index)
        & ~universe.index.isin(prefix_name.index)
    ]

    ranked = pd.concat([exact, prefix_symbol, prefix_name, substring_name])
    results = [
        UniverseEntry(symbol=row.symbol, name=row.name, series=row.series)
        for row in ranked.head(limit).itertuples(index=False)
    ]
    if len(results) >= limit:
        return results

    # Fall back to fuzzy matching on company name for typos / partial words not
    # covered by prefix/substring matching (e.g. "relaince", "hdfc bnk"). Skipped for
    # short, single-token, all-alphabetic queries (e.g. "AAPL") -- those are
    # indistinguishable from a deliberate (and possibly unsupported, e.g. a US ticker)
    # bare-symbol guess, and short strings score misleadingly high against long
    # company names under fuzzy matching, e.g. "AAPL" ~ "APL Apollo Tubes".
    looks_like_bare_ticker_guess = bare_query.isalpha() and " " not in query and len(bare_query) <= 4
    if looks_like_bare_ticker_guess:
        return results

    seen_symbols = {r.symbol for r in results}
    query_tokens = bare_query.split()
    scored = universe["name_tokens"].map(lambda tokens: _best_token_match_score(query_tokens, tokens))
    fuzzy_ranked = universe.assign(match_score=scored).sort_values("match_score", ascending=False)
    for row in fuzzy_ranked.itertuples(index=False):
        if row.match_score < _FUZZY_SCORE_THRESHOLD:
            break
        if row.symbol in seen_symbols:
            continue
        results.append(UniverseEntry(symbol=row.symbol, name=row.name, series=row.series))
        seen_symbols.add(row.symbol)
        if len(results) >= limit:
            break
    return results


def resolve_symbol(user_text: str) -> str | None:
    """Resolve free-text user input (name, bare symbol, or full symbol) to a canonical ticker.

    Returns the canonical `.NS`/`.BO`-suffixed symbol (or a benchmark index symbol), or
    None if nothing in the NSE universe matches. Callers never need to ask the user for
    a suffix -- this is the single place that decides it.
    """
    text = user_text.strip()
    if not text:
        return None
    text_upper = text.upper()

    # An explicit, well-formed suffix (.NS/.BO, or a benchmark index symbol) is the
    # user's own choice of exchange and is always honored as-is -- resolution below
    # only kicks in for bare names/symbols that don't already specify one. This
    # matters for .BO in particular: it's outside the bundled NSE-only snapshot, so
    # bare-name matching below would otherwise silently redirect it to NSE.
    if is_supported_symbol(text_upper):
        return text_upper

    universe = load_universe()
    bare = text_upper.removesuffix(".NS").removesuffix(".BO")
    exact_bare = universe[universe["symbol"].str.removesuffix(".NS") == bare]
    if not exact_bare.empty:
        return str(exact_bare.iloc[0]["symbol"])

    matches = search_universe(text, limit=1)
    if matches:
        return matches[0].symbol
    return None
