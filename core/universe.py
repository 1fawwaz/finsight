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

from core.config import BENCHMARKS, is_supported_symbol, get_logger

logger = get_logger(__name__)

_UNIVERSE_CSV = Path(__file__).resolve().parent / "data" / "nse_equity_list.csv"

_CORP_SUFFIX_RE = re.compile(r"\b(limited|ltd|company|corporation|corp)\b\.?", re.IGNORECASE)

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


def search_universe(query: str, limit: int = 8) -> list[UniverseEntry]:
    """Search the NSE universe by company name or ticker (partial, case-insensitive, fuzzy).

    Thin, behavior-preserving wrapper around `core.search_engine.search_stocks` -- the
    single consolidated search implementation for the whole app (see that module's
    docstring for the ranking tiers, indexing strategy, and migration rationale). Kept
    here, under this name, so every pre-existing caller (`core.ui_components`,
    `resolve_symbol` below, and transitively `core.chat`/`core.watchlist`
    /`core.symbol_registry`/`core.data_ingestion`) keeps working unchanged. Imports
    `search_stocks` lazily to avoid a circular import: `core.search_engine` imports
    `UniverseEntry`/`_clean_name`/`display_symbol`/`load_universe` from this module at
    its own module-load time, so this module can't import back from it at load time too.
    """
    from core.search_engine import search_stocks

    return [r.entry for r in search_stocks(query, limit=limit)]


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
