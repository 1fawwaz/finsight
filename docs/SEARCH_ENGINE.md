# FinSight Global Stock Search Engine

Architecture, API, and design-decision reference for `core/search_engine.py`, the
single consolidated stock-search implementation for the whole app. This is the
companion doc referenced from that module's own docstring; `SEARCH_QUALITY_REPORT.md`
(repo root) holds the real-world quality/performance evidence.

## 1. Repository inspection (Phase 0), before this work started

Grep across the repo before any code was written found exactly two independent
stock-matching implementations:

- `core/universe.py::search_universe` — prefix/substring/fuzzy matching over the
  bundled NSE reference CSV (`core/data/nse_equity_list.csv`, 2,384 real listings),
  used (directly or via `resolve_symbol`) by `core.chat`, `core.watchlist`,
  `core.symbol_registry`, `core.data_ingestion`, and `core.ui_components`.
- `core/ui_components.py::stock_search_and_pick` / `stock_picker` — the two UI-facing
  widgets used by every page that lets a user find/add/select a stock (Market
  Overview, Portfolio, Stock Analysis, AI Sentiment, ML Signals, and the home page's
  quick search).

No third implementation existed anywhere else (verified again after the migration —
see the "Migration verification" section below).

## 2. Layered architecture

```
search_stocks()                       <- the one public API every caller uses
    SearchIndex                        <- precomputed structures, built once, cached
        core.universe.load_universe()    <- data source: bundled NSE reference list
        core.queries.list_all_tickers()  <- data source: DB-tracked symbols not
                                             (yet, or ever) in the bundled snapshot
```

No UI component queries the DB or the universe directly for search purposes.
`core.universe.search_universe` is now a thin, behavior-preserving wrapper around
`search_stocks` (see `core/universe.py`); every existing caller of `search_universe`
or `resolve_symbol` was migrated automatically, with zero per-caller changes needed,
because the wrapper's signature and return type (`list[UniverseEntry]`) didn't change.
`core/ui_components.py`'s two picker widgets call `search_stocks` directly (not
through the wrapper) so they can also use `SearchResult.matched_substring` (UI
highlighting) and pass a real `context` dict (personalization).

## 3. Public API contract

```python
def search_stocks(
    query: str,
    filters: SearchFilters | None = None,
    limit: int = 20,
    context: dict | None = None,
) -> list[SearchResult]: ...
```

- `query`: raw user input, any case/punctuation/suffix.
- `filters`: `SearchFilters(exchange: str | None)` — see gaps below for what isn't supported.
- `context`: optional `{"recent_searches": [...], "watchlist_symbols": {...},
  "portfolio_symbols": {...}}`. Every key optional; omitting `context` entirely applies
  no boost.
- Returns `SearchResult(entry: UniverseEntry, tier: int, tier_label: str,
  fuzzy_score: float, matched_substring: str | None)`.

## 4. Ranking algorithm (10 tiers, exactly as specified)

1. Exact ticker match
2. Exact company name match
3. Company name starts with query
4. Ticker starts with query
5. Word-prefix within company name
6. Company name contains query
7. Ticker contains query
8. Fuzzy similarity score (rapidfuzz, per-token, threshold 65/100)
9. Market capitalization (see gap below — proxy only)
10. Alphabetical (final tie-break)

Within any tier, the personalization boost (section 6) and the tier-9 proxy are
applied before the final alphabetical tie-break, in that order.

**Tier 9 gap, documented rather than invented:** no market-cap column exists anywhere
in this repository (neither the bundled CSV nor the `Ticker` table). Tier 9 uses
membership in `core.config.DEFAULT_TICKERS` (the app's own curated large-cap
watchlist) as an explicit, labeled proxy — the same heuristic `core.universe`'s
original implementation already used to break ties (e.g. "relaince" → Reliance
Industries over smaller same-scoring "Reliance ___" companies), now formalized as its
own numbered tier instead of being folded into the fuzzy tier.

## 5. Input normalization

`normalize_query`: uppercases, strips a trailing `.NS`/`.BO` suffix, collapses `&`,
`-`, `.`, and repeated whitespace to single spaces. Applied identically to indexed
symbols/names in `_prepare_frame` — real NSE data has both tickers (`ARE&M`,
`GMRP&UI`, `GVT&D`, `IL&FSENGG`, `IL&FSTRANS`) and company names (e.g. "Amara Raja
Energy & Mobility Limited") containing `&`; normalizing only the query side would
silently fail to match these real, existing listings.

## 6. Optional filters and personalization — what's real vs. what's a documented gap

**Implemented, on real data:**
- `SearchFilters.exchange` ("NSE"/"BSE") — real, since the exchange is encoded in the
  symbol suffix itself.
- Personalization boosts (`context` dict): +0.3 recent search, +0.5 watchlist
  membership, +0.5 portfolio membership. All three signals already existed in the app
  for other purposes (`st.session_state["recent_searches"]`,
  `core.watchlist.get_all_watchlist_symbols`, `core.portfolio.get_all_held_symbols`) —
  no new tracking infrastructure was built.

**Not implemented — documented gaps, not invented data:**
- `sector` / `index-membership` (Nifty 100/500) filters: sector is only populated in
  the DB for tickers a user has already caused to be ingested, not the full
  ~2,385-symbol bundled universe, and no authoritative Nifty constituent dataset
  exists anywhere in this repository (the same gap Phase 1 Steps 6/7/9 hit and left
  explicitly blocked). Passing these filters would either silently return nothing for
  most of the universe or require fabricating membership data.
- "Frequently viewed" / "AI-analyzed history" personalization: no view-count or
  analysis-history table exists. Extension point: `_boost_score` in
  `core/search_engine.py` is the one place a new signal would be added, once such a
  table exists — the `context` dict is designed to accept new keys without any other
  code changing.

## 7. Indexing strategy — justified by measurement, not convention

`SearchIndex` builds two O(1) dict lookups (exact ticker, exact name) for tiers 1–2.
Tiers 3–7 reuse vectorized pandas string operations over the in-memory universe
DataFrame; tier 8 reuses `rapidfuzz` per-candidate scoring. A trie or inverted index
was evaluated and **not** built — see `SEARCH_QUALITY_REPORT.md` for the actual
measured numbers at both real (2,387-row) and synthetic 5x-target (11,935-row) scale,
which show the existing vectorized approach comfortably inside the 100ms budget with
real margin at both scales. Revisit if the universe grows well past 50,000 symbols or
fuzzy queries become a much larger share of traffic.

## 8. Cache lifecycle

- `get_index()`: lazily built on first use, cached (module-level singleton) after that.
- `add_symbol(symbol, name=None)`: incremental update — appends one row to the
  existing cached index without a full rebuild. Used for the common case (one new
  ticker ingested at a time). Idempotent for an already-indexed symbol.
- `refresh_index()`: full rebuild, for administrative use or as the automatic-recovery
  path if the cached index is ever found missing/corrupted (`_safe_index()` wraps
  every `search_stocks` call with this fallback). Never requires an app restart — the
  next `search_stocks` call after a refresh just uses the new index.

## 9. UX / accessibility

Search stays on Streamlit's native `st.text_input`/`st.selectbox` primitives rather
than a new custom HTML/JS component subsystem — these already provide real
keyboard/mouse/touch/screen-reader support without introducing a new, unaudited UI
layer. One real, measured limitation this implies: native `st.selectbox` options are
plain text, so matched-substring bolding can't render inside the dropdown itself.
Instead, `core/ui_components.py` shows the matched substring (bolded, via
`st.caption`, which does render markdown) once a result is picked, and shows
Watchlist/Portfolio membership as plain-text badges appended to each option's label.

## 10. Future compatibility (BSE, ETFs, mutual funds, indices, F&O, commodities, crypto, international exchanges)

None of these are built now -- building them without real data or a real need would be
exactly the kind of speculative infrastructure this project's engineering discipline
rules out. What matters is that the architecture doesn't *block* adding them later.
It doesn't, because of one structural fact: every tier of `search_stocks` (1-8) reads
only three generic columns off `SearchIndex.frame` -- `symbol`, `name`, `series` (plus
derived columns computed from them) -- and never branches on what *kind* of instrument
a row represents.

- **BSE**: already fully supported today (`SearchFilters.exchange="BSE"`, the explicit
  `.BO`-suffix branch in `search_stocks`, `_matches_exchange`). No further work needed.
- **ETFs**: NSE/BSE-listed ETFs already have ordinary `.NS`/`.BO` ticker symbols: if one
  is ingested via the existing `core.queries.list_all_tickers()` DB-supplement path (any
  `Ticker` row is automatically searchable, per `_supplement_from_db`), it's already
  searchable today with zero code changes. `UniverseEntry.series` is the field that
  would carry an "ETF" tag if a future data source distinguished one.
- **Mutual funds, indices (beyond the existing benchmark handful), futures & options,
  commodities, cryptocurrency, international exchanges**: none of these are equities,
  so none belong in the bundled NSE snapshot or the existing `Ticker` table. Each would
  need its own real data source (a scheme-code list, a contract-chain feed, a
  crypto-pair list, a foreign-exchange ticker list) -- exactly the same shape of work
  `build_index()` already does twice (`load_universe()` for the bundled CSV,
  `_supplement_from_db()` for DB-tracked tickers). Adding a third (or fourth, ...)
  source means writing one function that returns a DataFrame with the same
  `symbol`/`name`/`series` columns and concatenating it into `combined` in
  `build_index()` -- ranking, normalization, highlighting, and fuzzy matching all keep
  working unchanged, because none of that logic looks at `series` to decide *how* to
  match, only to break ties (tier 9) and to label results. `SearchFilters` is a
  dataclass with only-default-valued fields, so adding e.g. `asset_type: str | None =
  None` later is additive and never breaks an existing caller.

This is a description of the extension point, not a promise of a timeline -- nothing
here is built, and nothing should be built, until one of these asset classes has a real
data source and a real user-facing need.

## 11. Migration verification

Verified by repository-wide search (not assumed) after the migration: the only
substring-matching code left outside `core/search_engine.py` is
`pages/1_Market_Overview.py`'s watchlist "Filter by symbol or name" box, which filters
an *already-rendered* table of the user's own watchlist (by its own computed columns —
Price, RSI, 52-week range — that the search engine has no notion of), not a search
over the stock universe to find a new stock to add. This is documented in-line at that
call site. Every other call site (`core.chat`, `core.watchlist`,
`core.symbol_registry`, `core.data_ingestion`, `pages/3_Portfolio.py`, and both
`core.ui_components` picker widgets) goes through `search_stocks`, directly or via
`search_universe`'s thin wrapper.
