"""NSE market open/closed status in IST (approximate: trading hours only, no holiday calendar)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


@dataclass
class MarketStatus:
    is_open: bool
    label: str
    current_time_ist: datetime


def get_nse_market_status(now: datetime | None = None) -> MarketStatus:
    """Approximate NSE market status from trading hours (9:15-15:30 IST, Mon-Fri).

    Does not account for exchange holidays.
    """
    now_ist = (now.astimezone(IST) if now else datetime.now(IST))
    is_weekday = now_ist.weekday() < 5  # Mon=0 .. Fri=4
    is_trading_hours = MARKET_OPEN <= now_ist.time() <= MARKET_CLOSE
    is_open = is_weekday and is_trading_hours
    label = "Market Open" if is_open else "Market Closed"
    return MarketStatus(is_open=is_open, label=label, current_time_ist=now_ist)
