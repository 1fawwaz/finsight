"""Phase 2 Step 4: Market Breadth -- reusable, model-independent, market-wide features,
computed once per trading date across the tracked universe and persisted to
`market_breadth_daily` (not duplicated inside every symbol's own feature set).

Reuses `core.queries.get_price_history` (per-symbol history) and `core.indicators.ema`
(EMA computation) -- no price-fetching or indicator math duplicated here.
"""

from __future__ import annotations

import json
from datetime import date as date_type

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import get_logger
from core.database import MarketBreadthDaily
from core.indicators import ema
from core.queries import get_price_history

logger = get_logger(__name__)


def compute_market_breadth(symbols: list[str]) -> pd.DataFrame:
    """Cross-sectional market-wide statistics per trading date, across `symbols`.
    Returns a date-indexed DataFrame -- the pure computation, independent of
    persistence, so it's testable without a DB round-trip.
    """
    closes: dict[str, pd.Series] = {}
    for symbol in symbols:
        history = get_price_history(symbol)
        if not history.empty:
            closes[symbol] = history["close"]

    if not closes:
        return pd.DataFrame()

    close_df = pd.DataFrame(closes)  # columns = symbols, index = union of all dates
    returns_df = close_df.pct_change(fill_method=None)  # explicit: don't forward-fill gaps as if they were real trading

    advances = (returns_df > 0).sum(axis=1)
    declines = (returns_df < 0).sum(axis=1)
    # avoid divide-by-zero on a day with no declines at all (a genuinely all-green day)
    advance_decline_ratio = advances / declines.replace(0, pd.NA)

    rolling_high_252 = close_df.rolling(window=252, min_periods=20).max()
    rolling_low_252 = close_df.rolling(window=252, min_periods=20).min()
    new_highs = (close_df >= rolling_high_252).sum(axis=1)
    new_lows = (close_df <= rolling_low_252).sum(axis=1)

    ema20 = close_df.apply(lambda col: ema(col.dropna(), span=20)).reindex(close_df.index)
    ema50 = close_df.apply(lambda col: ema(col.dropna(), span=50)).reindex(close_df.index)
    ema200 = close_df.apply(lambda col: ema(col.dropna(), span=200)).reindex(close_df.index)
    pct_above_ema20 = (close_df > ema20).sum(axis=1) / close_df.notna().sum(axis=1)
    pct_above_ema50 = (close_df > ema50).sum(axis=1) / close_df.notna().sum(axis=1)
    pct_above_ema200 = (close_df > ema200).sum(axis=1) / close_df.notna().sum(axis=1)

    # Market momentum: equal-weighted composite's 20-day return.
    equal_weighted_composite = (1 + returns_df.mean(axis=1, skipna=True).fillna(0)).cumprod()
    market_momentum_20 = equal_weighted_composite.pct_change(20)

    # Participation: fraction of the tracked universe that advanced that day.
    market_participation = advances / close_df.notna().sum(axis=1)

    breadth = pd.DataFrame(
        {
            "universe_size": close_df.notna().sum(axis=1),
            "advance_decline_ratio": advance_decline_ratio,
            "new_highs": new_highs,
            "new_lows": new_lows,
            "pct_above_ema20": pct_above_ema20,
            "pct_above_ema50": pct_above_ema50,
            "pct_above_ema200": pct_above_ema200,
            "market_momentum_20": market_momentum_20,
            "market_participation": market_participation,
        }
    )
    return breadth


def persist_market_breadth(session, symbols: list[str]) -> int:
    """Compute and upsert `market_breadth_daily` rows for every date in `symbols`'
    combined history. Idempotent -- re-running overwrites the same dates with
    recomputed values (breadth is a derived fact, not append-only observed data, so
    overwriting on rerun is correct, unlike `prices`)."""
    breadth = compute_market_breadth(symbols)
    if breadth.empty:
        return 0

    symbols_json = json.dumps(sorted(symbols))
    rows = []
    for ts, row in breadth.iterrows():
        day: date_type = ts.date() if hasattr(ts, "date") else ts
        if pd.isna(row["universe_size"]) or row["universe_size"] == 0:
            continue
        rows.append(
            {
                "date": day,
                "universe_size": int(row["universe_size"]),
                "advance_decline_ratio": None if pd.isna(row["advance_decline_ratio"]) else float(row["advance_decline_ratio"]),
                "new_highs": int(row["new_highs"]),
                "new_lows": int(row["new_lows"]),
                "pct_above_ema20": None if pd.isna(row["pct_above_ema20"]) else float(row["pct_above_ema20"]),
                "pct_above_ema50": None if pd.isna(row["pct_above_ema50"]) else float(row["pct_above_ema50"]),
                "pct_above_ema200": None if pd.isna(row["pct_above_ema200"]) else float(row["pct_above_ema200"]),
                "market_momentum_20": None if pd.isna(row["market_momentum_20"]) else float(row["market_momentum_20"]),
                "market_participation": None if pd.isna(row["market_participation"]) else float(row["market_participation"]),
                "symbols_json": symbols_json,
            }
        )

    if not rows:
        return 0

    stmt = sqlite_insert(MarketBreadthDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["date"],
        set_={c: stmt.excluded[c] for c in (
            "universe_size", "advance_decline_ratio", "new_highs", "new_lows",
            "pct_above_ema20", "pct_above_ema50", "pct_above_ema200",
            "market_momentum_20", "market_participation", "symbols_json",
        )},
    )
    session.execute(stmt)
    session.flush()
    logger.info("Market breadth: persisted/updated %d date(s) across %d symbols", len(rows), len(symbols))
    return len(rows)


def get_market_breadth(session, start: date_type | None = None, end: date_type | None = None) -> pd.DataFrame:
    """Read back persisted market breadth, date-indexed -- the join target for any
    symbol-specific feature builder that wants market-wide context."""
    query = select(MarketBreadthDaily)
    if start is not None:
        query = query.where(MarketBreadthDaily.date >= start)
    if end is not None:
        query = query.where(MarketBreadthDaily.date <= end)
    rows = session.execute(query.order_by(MarketBreadthDaily.date)).scalars().all()
    if not rows:
        return pd.DataFrame()

    records = [
        {
            "date": r.date, "universe_size": r.universe_size, "advance_decline_ratio": r.advance_decline_ratio,
            "new_highs": r.new_highs, "new_lows": r.new_lows, "pct_above_ema20": r.pct_above_ema20,
            "pct_above_ema50": r.pct_above_ema50, "pct_above_ema200": r.pct_above_ema200,
            "market_momentum_20": r.market_momentum_20, "market_participation": r.market_participation,
        }
        for r in rows
    ]
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")
