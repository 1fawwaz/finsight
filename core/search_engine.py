"""FinSight Global Stock Search Engine.

Layered architecture (each layer built on the one below, never bypassed):

    search_stocks()               <- the one public API contract every caller uses
        SearchIndex                <- precomputed structures, built once, cached
            core.universe.load_universe()   <- data source: bundled NSE reference list
            core.queries.list_all_tickers() <- data source: DB-tracked symbols not
                                                (yet, or ever) in the bundled snapshot

Consolidates every stock-search code path in the app behind one function.
`core.universe.search_universe` becomes a thin, behavior-preserving wrapper around
this module (see the bottom of `core/universe.py`), so every existing caller
(`core.ui_components`, `core.universe.resolve_symbol`, and transitively
`core.chat`/`core.watchlist`/`core.symbol_registry`/`core.data_ingestion`) is migrated
automatically -- no per-caller changes were needed or made.

--- Ranking hierarchy (exactly as specified) --------------------------------------
1. Exact ticker match          6. Company name contains query
2. Exact company name match    7. Ticker contains query
3. Company name starts with    8. Fuzzy similarity score
4. Ticker starts with          9. Market capitalization
5. Word-prefix within name     10. Alphabetical (final tie-break)

Tier 9 (market capitalization) has no real data source anywhere in this
repository -- neither the bundled NSE snapshot nor the `Ticker` table carries a
market-cap column. Rather than fabricate one, this tier uses `DEFAULT_TICKERS`
membership (the app's own curated large-cap watchlist) as an honest, explicitly
labeled proxy -- the same heuristic `core.universe` already used pre-consolidation to
break a real tie ("relaince" -> Reliance Industries over smaller "Reliance ___"
companies), now applied consistently as its own numbered tier instead of being
folded into the fuzzy tier.

--- Indexing strategy, justified by measurement, not convention -------------------
`SearchIndex` builds two O(1) dict lookups (exact ticker, exact name) for tiers 1-2.
Tiers 3-7 reuse vectorized pandas string operations over the in-memory universe
DataFrame; tier 8 reuses `rapidfuzz` per-candidate scoring. A trie or inverted index
was evaluated and not built: real benchmarking (see `docs/SEARCH_ENGINE.md` /
`SEARCH_QUALITY_REPORT.md`) showed the existing vectorized approach completes in
3-12ms at the real 2,385-row universe and 11-48ms at a synthetic 11,930-row universe
(5x the directive's stated 10,000-symbol target) -- comfortably inside the 100ms
budget with real margin. A trie would only accelerate tiers 1-4 (already the fastest
tiers); the fuzzy tier (the actual worst-case cost driver) would need a
fundamentally different structure (BK-tree, n-gram index) that adds real complexity
the measured numbers don't currently justify. Revisit if the universe grows well
past 50,000 symbols or fuzzy queries become a much larger share of traffic.
"""

from __future__ import annotations

import re
import statistics
import time
from dataclasses import dataclass, field

import pandas as pd
from rapidfuzz import fuzz

from core.config import DEFAULT_TICKERS, get_logger, is_supported_symbol
from core.universe import UniverseEntry, _clean_name, display_symbol, load_universe

logger = get_logger(__name__)

DEFAULT_LIMIT = 20
FUZZY_SCORE_THRESHOLD = 65

_TIER_EXACT_TICKER = 1
_TIER_EXACT_NAME = 2
_TIER_NAME_PREFIX = 3
_TIER_TICKER_PREFIX = 4
_TIER_NAME_WORD_PREFIX = 5
_TIER_NAME_CONTAINS = 6
_TIER_TICKER_CONTAINS = 7
_TIER_FUZZY = 8
_TIER_LABELS = {
    _TIER_EXACT_TICKER: "exact_ticker",
    _TIER_EXACT_NAME: "exact_name",
    _TIER_NAME_PREFIX: "name_starts_with",
    _TIER_TICKER_PREFIX: "ticker_starts_with",
    _TIER_NAME_WORD_PREFIX: "name_word_prefix",
    _TIER_NAME_CONTAINS: "name_contains",
    _TIER_TICKER_CONTAINS: "ticker_contains",
    _TIER_FUZZY: "fuzzy",
}

# A short, all-alphabetic query is indistinguishable from a deliberate bare-ticker
# guess (possibly for an unsupported market) -- the same guard core.universe's
# original search_universe used, carried forward unchanged since it's still correct.
_BARE_TICKER_GUESS_MAX_LEN = 6


def normalize_query(text: str) -> str:
    """Normalize free-text input before matching: uppercase, strip a `.NS`/`.BO`
    suffix, and collapse `&`, `-`, `.`, and extra whitespace to single spaces -- per
    the directive's explicit normalization rule, applied once here rather than ad hoc
    per call site.

    Critically, the *identical* transformation is applied to indexed names/symbols
    in `_prepare_frame` below -- real NSE data has both company names containing "&"
    (e.g. "Amara Raja Energy & Mobility Limited") and, less obviously, tickers
    containing it too (`ARE&M`, `GMRP&UI`, `GVT&D`, `IL&FSENGG` -- confirmed by
    grepping the bundled CSV, not assumed). Normalizing only the query and comparing
    it against un-normalized names/symbols would silently fail to match any of these
    real, existing listings for a query typed without the punctuation.
    """
    text = text.strip().upper()
    text = text.removesuffix(".NS").removesuffix(".BO")
    text = re.sub(r"[&\-.]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass
class SearchIndex:
    """Precomputed structures built once from the stock universe. `frame` carries
    every column tiers 3-8 need (already-cleaned name, tokens, symbol variants);
    `by_symbol`/`by_name` give O(1) lookups for tiers 1-2.
    """

    frame: pd.DataFrame
    by_symbol: dict[str, int]
    by_name: dict[str, int]
    built_at: float
    build_source_counts: dict[str, int] = field(default_factory=dict)


_index: SearchIndex | None = None

# Index-health bookkeeping (core.search_engine.get_index_health) -- module-level,
# process-lifetime counters, never persisted; a process restart starts them fresh,
# which is correct since they describe *this* process's index/cache behavior.
_last_build_duration_ms: float | None = None
_last_build_ok = True
_last_build_error: str | None = None
_last_refresh_at: float | None = None
_cache_hits = 0
_cache_misses = 0
_MAX_LATENCY_SAMPLES = 200
_search_latencies_ms: list[float] = []


def _supplement_from_db() -> pd.DataFrame:
    """DB-tracked tickers not present in the bundled NSE snapshot -- e.g. a valid BSE
    symbol, or a symbol added before the snapshot was last refreshed. This is what
    makes "newly added symbols become searchable automatically, with no code
    changes" concretely true: adding a ticker to the DB (any existing ingestion path
    already does this) is enough, without touching the bundled CSV or this module.
    """
    from core.queries import list_all_tickers

    rows = list_all_tickers()
    if not rows:
        return pd.DataFrame(columns=["symbol", "name", "series"])
    df = pd.DataFrame(rows)
    df["name"] = df["name"].fillna(df["symbol"].map(display_symbol))
    df["series"] = "DB"
    return df[["symbol", "name", "series"]]


def _prepare_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Precompute every derived field once per universe build. `symbol_bare` and
    `name_clean_upper` both go through `normalize_query`'s exact transformation (not
    just `.upper()`) so a query normalized the same way lines up correctly against
    real "&"-containing names and tickers -- see `normalize_query`'s docstring.
    """
    frame = raw.copy().reset_index(drop=True)
    frame["symbol_bare"] = (
        frame["symbol"].str.removesuffix(".NS").str.removesuffix(".BO").map(normalize_query).str.replace(" ", "", regex=False)
    )
    frame["name_upper"] = frame["name"].str.upper()
    frame["name_clean_upper"] = frame["name"].map(_clean_name).map(normalize_query)
    frame["name_tokens"] = frame["name_clean_upper"].str.split()
    frame["is_default"] = frame["symbol"].isin(DEFAULT_TICKERS)
    return frame


def build_index() -> SearchIndex:
    """Build a fresh SearchIndex from the bundled universe plus the DB supplement.
    Always a full rebuild -- cheap enough (a few thousand rows, sub-100ms per the
    module docstring's benchmark) that a separate "full vs. incremental" rebuild path
    isn't warranted for the base build; `add_symbol` below is the incremental path
    used *between* rebuilds, for the common case of one new ticker at a time.

    Records build status/duration for `get_index_health()` and re-raises on failure
    (rather than swallowing it here) so `_safe_index`'s caller-facing fallback logic
    stays in one place.
    """
    global _last_build_duration_ms, _last_build_ok, _last_build_error
    start = time.time()
    try:
        base = load_universe()[["symbol", "name", "series"]]
        supplement = _supplement_from_db()
        supplement = supplement[~supplement["symbol"].isin(set(base["symbol"]))]

        combined = pd.concat([base, supplement], ignore_index=True)
        frame = _prepare_frame(combined)

        by_symbol = {row.symbol_bare: i for i, row in enumerate(frame.itertuples(index=False))}
        by_name = {
            row.name_clean_upper: i for i, row in enumerate(frame.itertuples(index=False)) if row.name_clean_upper
        }

        index = SearchIndex(
            frame=frame,
            by_symbol=by_symbol,
            by_name=by_name,
            built_at=time.time(),
            build_source_counts={"bundled": len(base), "db_supplement": len(supplement)},
        )
    except Exception as exc:
        _last_build_duration_ms = (time.time() - start) * 1000
        _last_build_ok = False
        _last_build_error = str(exc)
        logger.error("index_build_failed duration_ms=%.2f error=%s", _last_build_duration_ms, exc)
        raise

    _last_build_duration_ms = (time.time() - start) * 1000
    _last_build_ok = True
    _last_build_error = None
    logger.info(
        "index_build_complete bundled=%d db_supplement=%d total=%d duration_ms=%.2f",
        len(base), len(supplement), len(frame), _last_build_duration_ms,
    )
    return index


def get_index() -> SearchIndex:
    """The cached, lazily-built index -- built at first use (effectively "at
    application startup" for a Streamlit app, whose first page render is the first
    real request) and reused after that. See `refresh_index`/`add_symbol` for how it
    stays current without a full rebuild on every change.
    """
    global _index, _cache_hits, _cache_misses
    if _index is None:
        _cache_misses += 1
        _index = build_index()
    else:
        _cache_hits += 1
    return _index


def refresh_index() -> SearchIndex:
    """Manual/administrative full rebuild trigger, and the automatic-recovery path:
    call this if the cached index is ever found to be missing, stale, or corrupted.
    Never requires an application restart -- the next `search_stocks` call after a
    refresh just uses the new index.
    """
    global _index, _last_refresh_at, _cache_misses
    logger.info("index_rebuild_event trigger=manual_or_recovery")
    _index = build_index()
    _last_refresh_at = time.time()
    _cache_misses += 1
    return _index


def get_index_health() -> dict:
    """Real-time health snapshot of the search index: build status/error, symbol
    count, build/refresh timestamps, build duration, cache hit/miss counts and
    status, approximate in-memory footprint, and recent search latency. Never
    raises -- "no index built yet" is a real, valid state, not an error, and this is
    read by the autocomplete widget to distinguish a genuine index outage
    (`build_ok=False`) from an ordinary transient search error.
    """
    index = _index
    memory_bytes = int(index.frame.memory_usage(deep=True).sum()) if index is not None else 0
    return {
        "build_ok": _last_build_ok,
        "build_error": _last_build_error,
        "symbol_count": len(index.frame) if index is not None else 0,
        "built_at": index.built_at if index is not None else None,
        "last_refresh_at": _last_refresh_at,
        "build_duration_ms": _last_build_duration_ms,
        "cache_status": "hit" if index is not None else "miss",
        "cache_hits": _cache_hits,
        "cache_misses": _cache_misses,
        "memory_bytes": memory_bytes,
        "last_search_latency_ms": _search_latencies_ms[-1] if _search_latencies_ms else None,
        "mean_search_latency_ms": statistics.mean(_search_latencies_ms) if _search_latencies_ms else None,
    }


def add_symbol(symbol: str, name: str | None = None) -> None:
    """Incremental update: add exactly one newly-ingested symbol to the *existing*
    index without a full rebuild -- the common case (`core.data_ingestion
    .get_or_create_ticker` creating one new Ticker row at a time). Falls back to a
    full rebuild only if no index exists yet to update incrementally.
    """
    global _index
    if _index is None:
        _index = build_index()
        return

    bare = symbol.removesuffix(".NS").removesuffix(".BO").upper()
    if bare in _index.by_symbol:
        return  # already indexed -- idempotent, matches this app's upsert conventions elsewhere

    row = pd.DataFrame([{"symbol": symbol, "name": name or display_symbol(symbol), "series": "DB"}])
    row = _prepare_frame(row)
    new_row_position = len(_index.frame)
    _index.frame = pd.concat([_index.frame, row], ignore_index=True)
    _index.by_symbol[bare] = new_row_position
    name_key = row.iloc[0]["name_clean_upper"]
    if name_key:
        _index.by_name.setdefault(name_key, new_row_position)
    logger.info("Search index: incrementally added %s (no full rebuild)", symbol)


def _safe_index() -> SearchIndex:
    """Fallback strategy: if the cached index is unavailable or fails to build,
    rebuild it automatically and log the event, rather than surfacing an error to the
    caller. This is the one place `search_stocks` reaches for the index, so every
    caller gets this safety net for free.
    """
    try:
        return get_index()
    except Exception as exc:  # a corrupt/failed build must never break search entirely
        logger.warning("Search index unavailable (%s) -- rebuilding in the background.", exc)
        return refresh_index()


@dataclass
class SearchFilters:
    exchange: str | None = None  # "NSE" or "BSE" -- real, supported (symbol suffix)
    # Sector and index-membership filters are NOT implemented: sector is only
    # populated in the DB for tickers a user has already caused to be ingested (not
    # the ~2,385-symbol bundled universe), and no authoritative Nifty
    # index-constituent dataset exists anywhere in this repository (the same gap
    # Phase 1 Steps 6/7/9 hit and left blocked). Passing sector/index filters here
    # would either silently return nothing for 99% of the universe or require
    # fabricating membership data -- neither is acceptable, so these dimensions are
    # documented as unsupported rather than implemented on invented data.


def _matches_exchange(symbol: str, exchange: str | None) -> bool:
    if exchange is None:
        return True
    if exchange.upper() == "NSE":
        return symbol.endswith(".NS")
    if exchange.upper() == "BSE":
        return symbol.endswith(".BO")
    return True  # an unrecognized filter value is not silently treated as "no matches"


def _boost_score(symbol: str, context: dict | None) -> float:
    """Personalization boost, applied only using data that already exists in the
    application (per the directive's own instruction) -- recent searches
    (`st.session_state`, passed in via `context["recent_searches"]`), watchlist
    membership, and portfolio holdings. "Frequently viewed" and "AI-analyzed history"
    are NOT wired in: no view-count table exists (would require new tracking
    infrastructure, explicitly out of scope for this task), and no single
    "AI-analyzed" flag exists distinct from the other signals. Returns an additive
    boost folded into the sort key -- 0.0 (no effect) when `context` is None, so
    personalization is always optional, never required for basic search to work.
    """
    if not context:
        return 0.0
    boost = 0.0
    if symbol in (context.get("recent_searches") or ()):
        boost += 0.3
    if symbol in (context.get("watchlist_symbols") or ()):
        boost += 0.5
    if symbol in (context.get("portfolio_symbols") or ()):
        boost += 0.5
    return boost


@dataclass
class SearchResult:
    entry: UniverseEntry
    tier: int
    tier_label: str
    fuzzy_score: float
    matched_substring: str | None  # for UI highlighting -- the exact text that matched, in its original casing


def search_stocks(
    query: str,
    filters: SearchFilters | None = None,
    limit: int = DEFAULT_LIMIT,
    context: dict | None = None,
) -> list[SearchResult]:
    """The single public entry point for every stock search in FinSight. No other
    function anywhere in the codebase should query the stock universe or DB directly
    for search purposes -- see `docs/SEARCH_ENGINE.md` for the migration verification
    proving every existing call site goes through this (indirectly, via
    `core.universe.search_universe`'s now-thin wrapper).

    query: raw user input, any case/punctuation/suffix -- normalized internally.
    filters: optional SearchFilters (currently: exchange only -- see its docstring
        for why sector/index-membership aren't implemented).
    limit: maximum results to return.
    context: optional dict with "recent_searches"/"watchlist_symbols"/
        "portfolio_symbols" (each an iterable of symbols) for personalization boosts.
        Never required -- omitting it just means no boost is applied.

    Records its own latency into the rolling sample `get_index_health()` reports --
    query text itself is never logged (only its length, in the widget layer's own
    request log), so latency tracking here stays free of query content.
    """
    start = time.perf_counter()
    try:
        return _search_stocks_impl(query, filters=filters, limit=limit, context=context)
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        _search_latencies_ms.append(elapsed_ms)
        del _search_latencies_ms[: -_MAX_LATENCY_SAMPLES]


def _search_stocks_impl(
    query: str,
    filters: SearchFilters | None = None,
    limit: int = DEFAULT_LIMIT,
    context: dict | None = None,
) -> list[SearchResult]:
    normalized = normalize_query(query)
    if not normalized:
        return []

    index = _safe_index()
    frame = index.frame
    filters = filters or SearchFilters()

    bare_query = normalized.replace(" ", "")

    # An explicit, well-formed suffix the user typed themselves is always honored,
    # even for a symbol outside the bundled snapshot (e.g. a .BO symbol) -- mirrors
    # core.universe.resolve_symbol's original, already-tested behavior exactly.
    # Checked against the *real* (unnormalized) symbol column, not index.by_symbol
    # (whose keys are punctuation-stripped for fuzzy-friendly matching, per
    # normalize_query's docstring, and so can't be compared against a raw symbol
    # string directly). Recurses with the suffix stripped, not the original query --
    # recursing with the same query would re-enter this exact branch forever.
    upper_query = query.strip().upper()
    if (
        is_supported_symbol(upper_query)
        and upper_query not in set(frame["symbol"])
        and _matches_exchange(upper_query, filters.exchange)
    ):
        synthetic = UniverseEntry(symbol=upper_query, name=f"{display_symbol(upper_query)} (not in bundled reference list)", series="BO" if upper_query.endswith(".BO") else "NS")
        remainder = (
            _search_stocks_impl(bare_query, filters=filters, limit=limit - 1, context=context) if limit > 1 else []
        )
        return [SearchResult(synthetic, _TIER_EXACT_TICKER, "explicit_suffix", 100.0, upper_query)] + [
            r for r in remainder if r.entry.symbol != upper_query
        ]

    tiers = pd.Series(0, index=frame.index, dtype="int64")
    unmatched = pd.Series(True, index=frame.index)

    def _assign(mask: "pd.Series[bool]", tier: int) -> None:
        target = mask & unmatched
        tiers[target] = tier
        unmatched[target] = False

    _assign(frame["symbol_bare"] == bare_query, _TIER_EXACT_TICKER)
    _assign(frame["name_clean_upper"] == normalized, _TIER_EXACT_NAME)
    _assign(frame["name_clean_upper"].str.startswith(normalized), _TIER_NAME_PREFIX)
    _assign(frame["symbol_bare"].str.startswith(bare_query), _TIER_TICKER_PREFIX)
    _assign(
        frame["name_tokens"].map(lambda tokens: any(t.startswith(normalized) for t in tokens) if isinstance(tokens, list) else False),
        _TIER_NAME_WORD_PREFIX,
    )
    _assign(frame["name_clean_upper"].str.contains(re.escape(normalized), regex=True, na=False), _TIER_NAME_CONTAINS)
    _assign(frame["symbol_bare"].str.contains(re.escape(bare_query), regex=True, na=False), _TIER_TICKER_CONTAINS)

    looks_like_bare_ticker_guess = bare_query.isalpha() and " " not in normalized and len(bare_query) <= _BARE_TICKER_GUESS_MAX_LEN
    fuzzy_scores = pd.Series(0.0, index=frame.index)
    if not looks_like_bare_ticker_guess:
        query_tokens = normalized.split()
        candidates = unmatched
        if candidates.any():
            fuzzy_scores.loc[candidates] = frame.loc[candidates, "name_tokens"].map(
                lambda tokens: _token_match_score(query_tokens, tokens)
            )
            fuzzy_hits = candidates & (fuzzy_scores >= FUZZY_SCORE_THRESHOLD)
            tiers[fuzzy_hits] = _TIER_FUZZY
            unmatched[fuzzy_hits] = False

    matched = frame[~unmatched].copy()
    matched["tier"] = tiers[~unmatched]
    matched["fuzzy_score"] = fuzzy_scores[~unmatched]

    if filters.exchange is not None:
        matched = matched[matched["symbol"].map(lambda s: _matches_exchange(s, filters.exchange))]

    if matched.empty:
        return []

    matched["boost"] = matched["symbol"].map(lambda s: _boost_score(s, context))
    matched = matched.sort_values(
        by=["tier", "boost", "fuzzy_score", "is_default", "name"],
        ascending=[True, False, False, False, True],
    )

    results = []
    for row in matched.head(limit).itertuples(index=False):
        entry = UniverseEntry(symbol=row.symbol, name=row.name, series=row.series)
        results.append(
            SearchResult(
                entry=entry, tier=row.tier, tier_label=_TIER_LABELS.get(row.tier, "unknown"),
                fuzzy_score=row.fuzzy_score,
                matched_substring=_matched_substring_for(row.symbol, row.name, row.tier, bare_query, normalized),
            )
        )
    return results


_TICKER_TIERS = (_TIER_EXACT_TICKER, _TIER_TICKER_PREFIX, _TIER_TICKER_CONTAINS)


def _matched_substring_for(symbol: str, name: str, tier: int, bare_query: str, normalized: str) -> str | None:
    """Best-effort literal substring, in its original casing/punctuation, that
    produced the match -- for UI highlighting. Derived from the already-computed
    normalized query rather than threaded through the tiering loop as a side
    channel, since the mapping back to original text is deterministic. Tries the
    ticker first for ticker-matched tiers (allowing punctuation like the real "&" in
    tickers such as ARE&M to sit between matched characters), then the company name
    (same allowance for tokens separated by "&"/"-"/"." in the original text). None
    for the fuzzy tier, where there is no single literal substring to highlight.
    """
    if tier in _TICKER_TIERS and bare_query:
        pattern = "[^A-Za-z0-9]*".join(re.escape(ch) for ch in bare_query)
        match = re.search(pattern, symbol, re.IGNORECASE)
        if match:
            return match.group(0)
    if normalized:
        pattern = "[^A-Za-z0-9]*".join(re.escape(tok) for tok in normalized.split())
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _token_match_score(query_tokens: list[str], name_tokens: list[str] | float) -> float:
    """Average, over each query word, of its best fuzzy match against any word in the
    candidate company name -- unchanged from core.universe's original
    `_best_token_match_score`, moved here as part of the consolidation."""
    if not isinstance(name_tokens, list) or not query_tokens or not name_tokens:
        return 0.0
    return sum(max(fuzz.ratio(qt, nt) for nt in name_tokens) for qt in query_tokens) / len(query_tokens)
