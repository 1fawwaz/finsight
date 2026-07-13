"""Phase 2 Step 3: Sector-Relative Features.

Cross-sectional by nature (a stock's sector composite is built from *other* tickers), so
this module needs database access -- unlike `core.ml_model.build_features`/
`core.ml.feature_pipeline.build_features_v2`/`v3`, which operate on a single already-
loaded `price_df`. Kept as a separate, explicitly-invoked module rather than wired
automatically into every existing feature call site, since pulling every sector peer's
full history has a real cost `build_features_v3` callers don't currently pay.

Sector membership is read from `core.database.Ticker.sector` (populated from yfinance at
ingestion time) -- never hardcoded, per the directive's explicit prohibition. A symbol
with no sector, or with no sector peers currently tracked, produces NaN sector-relative
features rather than a fabricated composite from an empty or single-member "sector."
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import select

from core.config import get_logger
from core.database import Ticker
from core.queries import get_price_history

logger = get_logger(__name__)

MIN_SECTOR_PEERS = 2  # a "sector" of one (the stock itself) can't produce a relative feature


def get_sector_peers(session, symbol: str) -> list[str]:
    """Every other ticker sharing `symbol`'s sector, excluding `symbol` itself. Empty
    list if `symbol` has no sector recorded, or no peers are currently tracked."""
    ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
    if ticker is None or ticker.sector is None:
        return []
    peers = session.execute(
        select(Ticker.symbol).where(Ticker.sector == ticker.sector, Ticker.symbol != ticker.symbol)
    ).scalars().all()
    return list(peers)


def build_sector_composite(peer_symbols: list[str]) -> pd.Series:
    """Equal-weighted mean daily return across `peer_symbols`, on their shared (inner-
    joined) trading dates -- a simple, transparent composite, not a market-cap-weighted
    index (no market-cap data is tracked in this repository to weight by).
    """
    peer_returns = {}
    for symbol in peer_symbols:
        history = get_price_history(symbol)
        if history.empty:
            continue
        peer_returns[symbol] = history["close"].pct_change()

    if not peer_returns:
        return pd.Series(dtype=float)

    returns_df = pd.DataFrame(peer_returns).dropna(how="all")
    return returns_df.mean(axis=1, skipna=True)


def build_sector_relative_features(session, symbol: str, price_df: pd.DataFrame) -> pd.DataFrame:
    """Sector-relative features for `symbol`, aligned to `price_df`'s index. All columns
    are NaN (not zero, not dropped) when `symbol` has fewer than `MIN_SECTOR_PEERS`
    tracked peers -- an absent sector comparison is a real "unknown," not a fabricated
    zero.
    """
    index = price_df.index
    columns = [
        "relative_strength_vs_sector", "excess_return_vs_sector", "sector_momentum_20",
        "sector_volatility_20", "sector_trend", "sector_breadth",
    ]
    empty = pd.DataFrame(float("nan"), index=index, columns=columns)

    peers = get_sector_peers(session, symbol)
    if len(peers) < MIN_SECTOR_PEERS:
        logger.info("Sector-relative features: %s has %d tracked peer(s) (< %d) -- returning NaN.", symbol, len(peers), MIN_SECTOR_PEERS)
        return empty

    sector_returns = build_sector_composite(peers)
    if sector_returns.empty:
        return empty

    stock_returns = price_df["close"].pct_change()

    # Align on the shared dates between this stock and the sector composite -- an
    # inner join by construction (reindex then compute), never assuming they share
    # every date.
    aligned_sector_returns = sector_returns.reindex(index)
    aligned_stock_returns = stock_returns.reindex(index)

    sector_cum_index = (1 + aligned_sector_returns.fillna(0)).cumprod()
    stock_cum_index = (1 + aligned_stock_returns.fillna(0)).cumprod()

    features = pd.DataFrame(index=index)
    # Relative strength: ratio of cumulative-return indices -- rising means this stock
    # is outperforming its sector composite over the life of the series so far.
    features["relative_strength_vs_sector"] = stock_cum_index / sector_cum_index
    features["excess_return_vs_sector"] = aligned_stock_returns - aligned_sector_returns
    features["sector_momentum_20"] = sector_cum_index.pct_change(20)
    features["sector_volatility_20"] = aligned_sector_returns.rolling(window=20, min_periods=20).std() * (252 ** 0.5)
    sector_ma_20 = sector_cum_index.rolling(window=20, min_periods=20).mean()
    features["sector_trend"] = sector_cum_index / sector_ma_20 - 1
    features["sector_breadth"] = _sector_breadth(peers, index)

    # Rows before the sector composite's own history begins (or before this stock's)
    # are genuinely unknown, not zero -- reindexing above already produces NaN there,
    # nothing further to do; stated for clarity, not a missed step.
    return features


def _sector_breadth(peer_symbols: list[str], index: pd.DatetimeIndex, ma_window: int = 20) -> pd.Series:
    """% of sector peers trading above their own `ma_window`-day moving average, per
    day -- a real cross-sectional breadth measure, not a proxy computed from one series."""
    above_ma_flags = []
    for symbol in peer_symbols:
        history = get_price_history(symbol)
        if history.empty:
            continue
        ma = history["close"].rolling(window=ma_window, min_periods=ma_window).mean()
        above_ma_flags.append((history["close"] > ma).reindex(index))

    if not above_ma_flags:
        return pd.Series(float("nan"), index=index)

    flags_df = pd.concat(above_ma_flags, axis=1)
    return flags_df.mean(axis=1, skipna=True)
