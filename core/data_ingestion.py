"""Fetches OHLCV price history from yfinance and idempotently upserts it into the DB."""

from __future__ import annotations

from datetime import date as date_type
from typing import Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import DEFAULT_TICKERS, HISTORY_PERIOD, UNSUPPORTED_MARKET_MESSAGE, get_logger, is_supported_symbol
from core.database import Price, Ticker, get_session, init_db
from core.symbol_registry import get_or_create as get_or_create_symbol_registry_entry
from core.universe import resolve_symbol

logger = get_logger(__name__)


class IngestionError(Exception):
    """Raised when a ticker's data cannot be fetched or is invalid."""


def get_or_create_ticker(session, symbol: str) -> Ticker:
    """Fetch an existing Ticker row by symbol or create one, filling name/sector from yfinance.

    `symbol` may be a company name, bare ticker, or full `.NS`/`.BO` symbol -- it is
    resolved to a canonical symbol via `core.universe.resolve_symbol` first, so callers
    never need to know or type the exchange suffix themselves. This is the single
    creation path for every "add a stock" flow in the app (watchlist, portfolio,
    sentiment, ML), which is what keeps the Ticker table a single source of truth
    with no duplicate rows for the same symbol.

    The create step is a real INSERT ... ON CONFLICT DO NOTHING (not a bare insert),
    because two concurrent first-time adds of the same new symbol could otherwise both
    pass the SELECT above before either commits, and a bare insert would then raise an
    unhandled IntegrityError on the unique `symbol` constraint.
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

    session.execute(
        sqlite_insert(Ticker).values(symbol=symbol, name=name, sector=sector).on_conflict_do_nothing(index_elements=["symbol"])
    )
    session.flush()
    return session.execute(select(Ticker).where(Ticker.symbol == symbol)).scalar_one()


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


def upsert_prices(session, ticker: Ticker, history: pd.DataFrame, internal_id: str | None = None) -> int:
    """Insert new price rows for a ticker, skipping dates already present. Returns rows inserted.

    `internal_id` (optional, backward compatible -- existing callers passing only
    `session`/`ticker`/`history` are unaffected) stamps the Phase 1 permanent-identity
    key onto each inserted row and, when provided, extends the dedup check to also skip
    any date already present under that `internal_id` via a *different* `ticker_id` --
    the case a ticker rename produces (two `Ticker` rows, one `internal_id`), per
    docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md §7.3. Without an `internal_id`, dedup
    behavior is byte-identical to before this parameter existed.
    """
    existing_dates = set(
        session.execute(select(Price.date).where(Price.ticker_id == ticker.id)).scalars().all()
    )
    if internal_id is not None:
        existing_dates |= set(
            session.execute(select(Price.date).where(Price.internal_id == internal_id)).scalars().all()
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
                internal_id=internal_id,
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
    """Fetch and idempotently store price history for a single ticker. Returns rows inserted.

    `symbol` is resolved to its canonical `.NS`/`.BO` form (name, bare ticker, or full
    symbol all accepted) once via `get_or_create_ticker`, and that canonical symbol --
    not the raw input -- is what's used to fetch history, so e.g. passing "reliance"
    fetches RELIANCE.NS rather than asking yfinance for the literal string "reliance".

    Also resolves (and creates, if new) the symbol's permanent Symbol Registry entry and
    stamps every inserted row with its `internal_id` (Phase 1, spec §7.3/§7.7) -- this is
    additive to the existing `Ticker`-based flow, not a replacement for it; every
    existing caller of this function keeps working unchanged.
    """
    with get_session() as session:
        ticker = get_or_create_ticker(session, symbol)
        registry_entry = get_or_create_symbol_registry_entry(session, ticker.symbol)
        history = fetch_price_history(ticker.symbol, period=period)
        inserted = upsert_prices(session, ticker, history, internal_id=registry_entry.internal_id)
        logger.info(
            "%s: inserted %d new price rows (internal_id=%s)", ticker.symbol, inserted, registry_entry.internal_id
        )
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
