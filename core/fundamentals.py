"""Live fundamental snapshot (market cap, P/E, dividend yield, 52-week range) for a
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
    never substitute a fabricated or zero value."""

    market_cap: float | None
    pe_ratio: float | None
    dividend_yield: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    available: bool


_UNAVAILABLE = Fundamentals(None, None, None, None, None, available=False)


def _normalize_dividend_yield(raw: float | None) -> float | None:
    """yfinance has inconsistently expressed `dividendYield` as either a fraction
    (0.0325 for 3.25%) or an already-multiplied percentage (3.25) across versions and
    even across tickers within the same version -- confirmed empirically (yfinance
    1.5.1 returned 9.69 for a real ~9.7% yield, not 0.0969). No real equity sustains a
    dividend yield anywhere near 100%, so treat any value over 1 as already being a
    percentage and rescale it, rather than trusting the API's units blindly."""
    if raw is None:
        return None
    return raw / 100 if raw > 1 else raw


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
            dividend_yield=_normalize_dividend_yield(info.get("dividendYield")),
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
