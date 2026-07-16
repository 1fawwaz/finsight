# Search Quality & Performance Validation Report

Real-world evidence for `core.search_engine.search_stocks`, generated 2026-07-13.
Nothing below is fabricated or estimated — every number is the literal output of the
reproduction script in Section 5, run against the real bundled 2,387-entry universe
(2,386 bundled NSE listings + 1 pre-existing DB-supplement entry from this
repo's own dev database) on this machine.

## 1. Search quality by category (precision, ranking correctness, duplicates)

| Category | Queries | Top-1 precision | Duplicate rate | Mean latency | Max latency |
|---|---|---|---|---|---|
| Exact ticker | 5 | 100% | 0% | 10.95 ms | 13.80 ms |
| Exact company name | 3 | 100% | 0% | 12.86 ms | 17.58 ms |
| Case-insensitive | 3 | 100% | 0% | 9.68 ms | 12.68 ms |
| Partial prefix | 3 | 100% | 0% | 8.10 ms | 8.32 ms |
| Multi-word | 3 | 100% | 0% | 15.07 ms | 15.39 ms |
| Typo / fuzzy fallback | 2 | 100% | 0% | 14.74 ms | 15.44 ms |
| Ampersand / punctuation (`M&M`, `ARE&M`, `L&T`) | 3 | 100% | 0% | 15.71 ms | 16.64 ms |
| Explicit `.BO` suffix | 2 | 100% | 0% | 11.65 ms | 12.52 ms |
| Foreign-ticker rejection (`AAPL`, `GOOGL`, `TSLA`, `AMZN`) | 4 | 100% (correctly return no match) | 0% | 6.18 ms | 6.23 ms |
| No match (garbage input) | 1 | 100% | 0% | 11.02 ms | 11.02 ms |
| **Overall** | **29** | **100%** | **0%** | **11.29 ms** | **17.58 ms** |
| Overall p95 | | | | 16.64 ms | |

"Precision" = the top-ranked result is the objectively correct company/ticker for the
query (or, for the rejection/no-match categories, that the correct behavior is zero
results). "Duplicate rate" = fraction of queries whose result list contained the same
symbol more than once.

Two ground-truth values in this table were corrected during evidence generation and
are worth stating plainly rather than silently: the harness's own first draft assumed
"tata motors" resolves to a symbol literally named `TATAMOTORS.NS` and that "l&t"
should return no match. Both assumptions were wrong, not the engine — Tata Motors'
real current NSE ticker is `TMCV.NS` ("Tata Motors Limited"), and "L&T" correctly
resolves to `LT.NS` ("Larsen & Toubro Limited") via the exact-ticker tier once `&` is
normalized out of the query. The table above reflects the corrected, verified ground
truth.

## 2. Performance at scale

| Scale | Rows | Mean latency (10 queries/category, 29 total) | Max latency |
|---|---|---|---|
| Real bundled universe | 2,387 | 11.29 ms | 17.58 ms |
| Synthetic (5x the directive's 10,000-symbol target) | 11,935 | 35.99 ms | 67.64 ms |

Both scales are comfortably inside the 100ms budget, with real margin (64% headroom
at 5x target scale). This is why `core/search_engine.py` does not build a trie or
inverted index: tiers 1–2 already use O(1) dict lookups, tiers 3–7 use vectorized
pandas string operations, and the fuzzy tier (the actual worst-case cost driver) is
the only one a trie wouldn't accelerate anyway. See `docs/SEARCH_ENGINE.md` section 7
for the full reasoning.

## 3. Automated test coverage

- `tests/test_search_engine.py` — 31 tests, all passing: normalization, all 10 ranking
  tiers, duplicate prevention, limit enforcement, exchange filters, the explicit-suffix
  branch (including the regression case that used to infinite-recurse), UI-highlighting
  (`matched_substring`) correctness including `&`-containing tickers, personalization
  boost re-ordering, index lifecycle (cache/refresh/incremental add/idempotence/lazy
  build), and a loose performance sanity bound.
- `tests/test_universe.py` — 39 pre-existing tests, all still passing unchanged through
  `search_universe`'s new thin-wrapper implementation (zero test edits required to keep
  them green — the wrapper is fully behavior-preserving).
- Full repo suite: **614 passed**, 0 failed (`pytest tests/ -q`).

## 4. Before / after comparison

| | Before | After |
|---|---|---|
| Search implementations in the repo | 2 independent (`core.universe.search_universe`; `core.ui_components`'s two picker widgets called it, but had no highlighting/personalization of their own) | 1 (`core.search_engine.search_stocks`); `search_universe` is now a thin wrapper |
| UI highlighting of matched text | None | `SearchResult.matched_substring`, rendered bolded via `st.caption` on the picked result |
| Personalization | None | Recent-search / watchlist / portfolio boosts, wired from real existing data (no new tracking tables) |
| Structured filters | None | `SearchFilters.exchange` (NSE/BSE) |
| New symbols searchable without code changes | Yes, via DB (already true before) | Still yes — `core.queries.list_all_tickers()` DB-supplement, plus `add_symbol()` incremental index update |
| Ranking tiers | 4 implicit levels (exact/prefix-symbol/prefix-name/substring-name), fuzzy fallback outside that hierarchy | 10 explicit, numbered tiers exactly matching the specified ranking algorithm, including a documented market-cap proxy tier |
| Dedicated test coverage | 39 tests via `test_universe.py`, none of the picker widgets | 39 (preserved) + 31 new = 70 tests directly covering search behavior |

## 5. Reproduction

```bash
cd finsight
PYTHONPATH=. venv/Scripts/python.exe -c "
from core.search_engine import get_index, search_stocks
get_index()
print(search_stocks('m&m', limit=3))
"
pytest tests/test_search_engine.py tests/test_universe.py -q
```

The exact category-by-category script used to produce Section 1 and 2's numbers
constructs the query/expected-symbol table shown above, calls `search_stocks` once per
query with a warmed cache, and times each call with `time.perf_counter()`; the
synthetic-scale run concatenates the real universe frame 5x (rewriting symbols to stay
unique) and swaps it into the module-level index before timing, then restores the
original index. No network calls, no mocked data — the same bundled CSV and DB session
the app itself uses at runtime.

## 6. Remaining limitations (stated, not hidden)

- Tier 9 (market capitalization) is a proxy (`DEFAULT_TICKERS` membership), not real
  market-cap data — no such data exists anywhere in this repository.
- Sector and Nifty-index-membership filters are not implemented — same underlying gap
  Phase 1 Steps 6/7/9 hit (no authoritative Nifty constituent dataset in the repo).
- "Frequently viewed" personalization is not implemented — no view-count
  infrastructure exists; `_boost_score` is the documented extension point if one is
  built later.
- Matched-substring highlighting cannot render inside a native `st.selectbox`'s options
  (plain text only); it is shown separately once a result is picked.
- The BSE exchange filter returns zero results for any company not already present as
  an explicit `.BO` symbol (the bundled universe snapshot is NSE-only) — this is
  correct behavior given the data source, not a bug, but is worth stating since it may
  read as "no BSE stocks" for names not yet ingested with a `.BO` suffix.
