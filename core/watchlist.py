"""Watchlist CRUD, backed by the DB (not session state) so it persists across restarts
and is shared consistently everywhere a stock reference is needed, the same way
portfolio holdings already are."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.config import DEFAULT_TICKERS, get_logger
from core.data_ingestion import ingest_ticker
from core.database import Ticker, Watchlist, get_session
from core.universe import resolve_symbol

logger = get_logger(__name__)


def list_watchlist() -> list[dict]:
    """Watchlist tickers as {id, symbol, name, sector} dicts, oldest-added first.

    Eager-loads each entry's `ticker` relationship in one batched query
    (`selectinload`) instead of lazy-loading it one row at a time -- the same N+1
    pattern found and fixed in `core.portfolio.list_holdings` this session (see
    `BUG_FIX_REPORT.md`), independently present here too.
    """
    with get_session() as session:
        rows = session.execute(
            select(Watchlist).join(Ticker).order_by(Watchlist.added_at).options(selectinload(Watchlist.ticker))
        ).scalars().all()
        return [
            {"id": w.id, "symbol": w.ticker.symbol, "name": w.ticker.name, "sector": w.ticker.sector}
            for w in rows
        ]


def get_all_watchlist_symbols() -> set[str]:
    """Every symbol currently on the watchlist, as a set -- for the search engine's
    personalization boost, which only needs O(1) membership, not the full row detail
    `list_watchlist` returns."""
    with get_session() as session:
        rows = session.execute(
            select(Ticker.symbol).join(Watchlist, Watchlist.ticker_id == Ticker.id).distinct()
        ).scalars().all()
        return set(rows)


def is_in_watchlist(symbol: str) -> bool:
    """True if `symbol` (already-canonical) is on the watchlist."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return False
        existing = session.execute(
            select(Watchlist).where(Watchlist.ticker_id == ticker.id)
        ).scalar_one_or_none()
        return existing is not None


def add_to_watchlist(symbol: str) -> tuple[bool, str]:
    """Add `symbol` to the watchlist, ingesting its price history if it's new to the app.

    Returns (added, message). Adding an already-present symbol is a graceful no-op
    (added=False with a friendly message), never an error or a duplicate row. Raises
    `IngestionError` (from `core.data_ingestion`) if the symbol can't be resolved or
    its history can't be fetched -- callers already handle that the same way as every
    other "add a stock" flow in the app.
    """
    ingest_ticker(symbol)  # idempotent: ensures the Ticker row + price history exist
    canonical = resolve_symbol(symbol.strip()) or symbol.strip().upper()
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == canonical)).scalar_one()
        existing = session.execute(
            select(Watchlist).where(Watchlist.ticker_id == ticker.id)
        ).scalar_one_or_none()
        if existing is not None:
            return False, f"{ticker.symbol} is already in your watchlist."
        session.add(Watchlist(ticker_id=ticker.id))
        return True, f"Added {ticker.symbol} to your watchlist."


def remove_from_watchlist(symbol: str) -> None:
    """Remove a symbol from the watchlist. No-op if it isn't present."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return
        entry = session.execute(
            select(Watchlist).where(Watchlist.ticker_id == ticker.id)
        ).scalar_one_or_none()
        if entry is not None:
            session.delete(entry)


def seed_default_watchlist_if_empty() -> None:
    """On a fresh DB, seed the watchlist with the default large-cap tickers.

    Only runs when the watchlist table is completely empty, so it never overwrites
    or re-adds anything a user has deliberately removed.
    """
    with get_session() as session:
        has_any = session.execute(select(Watchlist.id).limit(1)).scalar_one_or_none()
        if has_any is not None:
            return
    for symbol in DEFAULT_TICKERS:
        try:
            add_to_watchlist(symbol)
        except Exception as exc:  # noqa: BLE001 -- seeding must never crash app startup
            logger.warning("Could not seed default watchlist ticker %s: %s", symbol, exc)
