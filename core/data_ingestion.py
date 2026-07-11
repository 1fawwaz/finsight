"""Fetches OHLCV price history from yfinance and idempotently upserts it into the DB."""

from __future__ import annotations

from datetime import date as date_type
from typing import Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from core.config import DEFAULT_TICKERS, HISTORY_PERIOD, UNSUPPORTED_MARKET_MESSAGE, get_logger, is_supported_symbol
from core.database import Price, Ticker, get_session, init_db
from core.universe import resolve_symbol

logger = get_logger(__name__)


class IngestionError(Exception):
    """Raised when a ticker's data cannot be fetched or is invalid."""


def get_or_create_ticker(session, symbol: str) -> Ticker:
    """Fetch an existing Ticker row by symbol or create one, filling name/sector from yfinance.

    `symbol` may be a company name, bare ticker, or full `.NS`/`.BO` symbol -- it is
    resolved to a canonical symbol via `core.universe.resolve_symbol` first, so callers
    never need to know or type the exchange suffix themselves.
    """
    resolved = resolve_symbol(symbol.strip())
    symbol = (resolved or symbol).upper().strip()
    if not is_supported_symbol(symbol):
        raise IngestionError(UNSUPPORTED_MARKET_MESSAGE)
    ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol)).scalar_one_or_none()
    if ticker is not None:
        return ticker

    name: Optional[str] = None
    sector: Optional[str] = None
    try:
        info = yf.Ticker(symbol).info
        name = info.get("shortName") or info.get("longName")
        sector = info.get("sector")
    except Exception as exc:  # yfinance/network errors shouldn't block ingestion
        logger.warning("Could not fetch metadata for %s: %s", symbol, exc)

    ticker = Ticker(symbol=symbol, name=name, sector=sector)
    session.add(ticker)
    session.flush()
    return ticker


def _validate_history(symbol: str, history: pd.DataFrame) -> None:
    if history is None or history.empty:
        raise IngestionError(f"No price history returned for {symbol!r}")
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    missing = required_cols - set(history.columns)
    if missing:
        raise IngestionError(f"{symbol!r} history missing columns: {missing}")


def fetch_price_history(symbol: str, period: str = HISTORY_PERIOD) -> pd.DataFrame:
    """Download OHLCV history for a symbol from yfinance and validate it."""
    history = yf.Ticker(symbol).history(period=period, auto_adjust=False)
    _validate_history(symbol, history)
    return history


def upsert_prices(session, ticker: Ticker, history: pd.DataFrame) -> int:
    """Insert new price rows for a ticker, skipping dates already present. Returns rows inserted."""
    existing_dates = set(
        session.execute(select(Price.date).where(Price.ticker_id == ticker.id)).scalars().all()
    )

    inserted = 0
    for ts, row in history.iterrows():
        bar_date: date_type = ts.date() if hasattr(ts, "date") else ts
        if bar_date in existing_dates:
            continue
        if any(pd.isna(row[col]) for col in ("Open", "High", "Low", "Close", "Volume")):
            continue
        session.add(
            Price(
                ticker_id=ticker.id,
                date=bar_date,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
            )
        )
        existing_dates.add(bar_date)
        inserted += 1
    return inserted


def ingest_ticker(symbol: str, period: str = HISTORY_PERIOD) -> int:
    """Fetch and idempotently store price history for a single ticker. Returns rows inserted."""
    with get_session() as session:
        ticker = get_or_create_ticker(session, symbol)
        history = fetch_price_history(symbol, period=period)
        inserted = upsert_prices(session, ticker, history)
        logger.info("%s: inserted %d new price rows", symbol, inserted)
        return inserted


def ingest_default_tickers(period: str = HISTORY_PERIOD) -> dict[str, int]:
    """Ingest price history for all DEFAULT_TICKERS. Returns a symbol -> rows_inserted map."""
    init_db()
    results: dict[str, int] = {}
    for symbol in DEFAULT_TICKERS:
        try:
            results[symbol] = ingest_ticker(symbol, period=period)
        except IngestionError as exc:
            logger.error("Skipping %s: %s", symbol, exc)
            results[symbol] = 0
    return results


if __name__ == "__main__":
    summary = ingest_default_tickers()
    total = sum(summary.values())
    logger.info("Ingestion complete: %d total new rows across %d tickers", total, len(summary))
