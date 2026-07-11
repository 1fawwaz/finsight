"""Read-side DB queries shared by Streamlit pages. Keeps SQL out of the presentation layer."""

from __future__ import annotations

from typing import Optional

import pandas as pd
from sqlalchemy import select

from core.database import Price, Ticker, get_session


def list_ticker_symbols() -> list[str]:
    """All ticker symbols currently stored in the database, sorted alphabetically."""
    with get_session() as session:
        return sorted(session.execute(select(Ticker.symbol)).scalars().all())


def get_ticker_info(symbol: str) -> Optional[dict]:
    """Return {symbol, name, sector} for a ticker, or None if it isn't in the DB."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return None
        return {"symbol": ticker.symbol, "name": ticker.name, "sector": ticker.sector}


def get_price_history(symbol: str) -> pd.DataFrame:
    """Full stored OHLCV history for a symbol as a date-indexed DataFrame. Empty if not found."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        rows = session.execute(
            select(Price).where(Price.ticker_id == ticker.id).order_by(Price.date)
        ).scalars().all()

    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        [
            {
                "date": r.date,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    )
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def get_multi_symbol_close(symbols: list[str]) -> pd.DataFrame:
    """Close-price DataFrame with one column per symbol, aligned on shared dates (inner join)."""
    frames = {}
    for symbol in symbols:
        history = get_price_history(symbol)
        if not history.empty:
            frames[symbol] = history["close"]
    if not frames:
        return pd.DataFrame()
    return pd.DataFrame(frames).dropna(how="any")
