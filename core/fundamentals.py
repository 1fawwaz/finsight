"""Live fundamental snapshot (market cap, P/E, dividend rate, 52-week range) for a
symbol, fetched from yfinance with a short in-memory cache.

Unlike OHLCV history, fundamentals aren't persisted to SQLite: they're only meaningful
as of "right now" (P/E moves with price daily), so caching them durably would risk
silently serving stale numbers. The in-memory TTL cache here is the "Memory" tier of the
SQLite -> Memory -> Network -> Gemini hierarchy for this specific kind of data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import yfinance as yf

from core.config import get_logger

logger = get_logger(__name__)

_CACHE_TTL_SECONDS = 3600
_cache: dict[str, tuple[float, "Fundamentals"]] = {}


@dataclass(frozen=True)
class Fundamentals:
    """A best-effort fundamentals snapshot. `available=False` means the fetch failed or
    returned nothing usable -- callers must treat every field as unknown in that case,
    never substitute a fabricated or zero value.

    `dividend_rate` is the trailing annual dividend in rupees per share, not a
    pre-computed yield percentage -- yfinance's own `dividendYield` field was confirmed
    empirically to disagree with `dividend_rate / price` for some tickers (e.g. WIPRO:
    field said 9.69, but rate/price says ~6.3, matching the real declared dividend).
    Callers should divide by the current price themselves to get a yield they can trust.
    """

    market_cap: float | None
    pe_ratio: float | None
    dividend_rate: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    available: bool


_UNAVAILABLE = Fundamentals(None, None, None, None, None, available=False)


def get_fundamentals(symbol: str) -> Fundamentals:
    """Best-effort fundamentals snapshot for `symbol`, cached for up to an hour."""
    now = time.monotonic()
    cached = _cache.get(symbol)
    if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        info = yf.Ticker(symbol).info
        result = Fundamentals(
            market_cap=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            dividend_rate=info.get("trailingAnnualDividendRate"),
            fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
            fifty_two_week_low=info.get("fiftyTwoWeekLow"),
            available=True,
        )
    except Exception as exc:  # yfinance/network errors must never break the caller
        logger.warning("Could not fetch fundamentals for %s: %s", symbol, exc)
        result = _UNAVAILABLE

    _cache[symbol] = (now, result)
    return result


def clear_cache() -> None:
    """Drop all cached fundamentals. Exposed for tests; production code relies on the TTL."""
    _cache.clear()
