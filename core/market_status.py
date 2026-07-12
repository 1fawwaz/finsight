"""NSE trading calendar: session status, holiday awareness, and next/previous trading
session in IST.

Holiday dates are sourced from NSE/exchange-published equity-segment holiday calendars
and must be extended manually once each year's list is published -- see
`_NSE_HOLIDAYS` below. For a year with no published list yet, holiday awareness
degrades to weekday-only (logged once, never a silent guess dressed up as fact).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from core.config import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
PRE_OPEN_START = time(9, 0)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
POST_CLOSE_END = time(16, 0)

# NSE equity-segment trading holidays. Source: NSE/exchange-published holiday
# calendars (e.g. https://www.nseindia.com -> Markets -> Holidays), cross-checked
# against secondary broker-published calendars. Extend with next year's list once
# NSE publishes it; do not guess future years.
_NSE_HOLIDAYS: dict[int, dict[date, str]] = {
    2026: {
        date(2026, 1, 26): "Republic Day",
        date(2026, 3, 3): "Holi",
        date(2026, 3, 26): "Shri Ram Navami",
        date(2026, 3, 31): "Shri Mahavir Jayanti",
        date(2026, 4, 3): "Good Friday",
        date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
        date(2026, 5, 1): "Maharashtra Day",
        date(2026, 5, 28): "Bakri Id",
        date(2026, 6, 26): "Muharram",
        date(2026, 9, 14): "Ganesh Chaturthi",
        date(2026, 10, 2): "Mahatma Gandhi Jayanti",
        date(2026, 10, 20): "Dussehra",
        date(2026, 11, 10): "Diwali-Balipratipada",
        date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
        date(2026, 12, 25): "Christmas",
    },
}

_warned_years: set[int] = set()


class SessionPhase(str, Enum):
    PRE_OPEN = "pre_open"
    OPEN = "open"
    POST_CLOSE = "post_close"
    CLOSED = "closed"


@dataclass
class MarketStatus:
    is_open: bool
    phase: SessionPhase
    label: str
    current_time_ist: datetime
    holiday_name: str | None
    is_trading_day: bool
    next_trading_day: date
    previous_trading_day: date
    holiday_data_available: bool


def holiday_name(day: date) -> str | None:
    """The NSE holiday name for `day`, or None if it isn't a published holiday."""
    year_table = _NSE_HOLIDAYS.get(day.year)
    if year_table is None and day.year not in _warned_years:
        _warned_years.add(day.year)
        logger.warning(
            "No published NSE holiday calendar for %s -- falling back to weekday-only "
            "trading-day checks for that year.",
            day.year,
        )
    return (year_table or {}).get(day)


def has_holiday_data(year: int) -> bool:
    return year in _NSE_HOLIDAYS


def is_trading_day(day: date) -> bool:
    """True if NSE equities trade on this date: a weekday that isn't a published holiday."""
    return day.weekday() < 5 and holiday_name(day) is None


def next_trading_day(from_date: date) -> date:
    """The first trading day strictly after `from_date`."""
    candidate = from_date + timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def previous_trading_day(from_date: date) -> date:
    """The last trading day strictly before `from_date`."""
    candidate = from_date - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def prediction_target_session(now: datetime | None = None) -> date:
    """The trading session a "predict next/tomorrow" question should target.

    Always the next trading day strictly after today in IST -- e.g. asked on a Sunday,
    this resolves to Monday (or the next open day, if Monday is itself a holiday).
    """
    now_ist = now.astimezone(IST) if now else datetime.now(IST)
    return next_trading_day(now_ist.date())


def get_nse_market_status(now: datetime | None = None) -> MarketStatus:
    """Current NSE session status in IST, aware of weekends and published holidays."""
    now_ist = now.astimezone(IST) if now else datetime.now(IST)
    today = now_ist.date()
    today_holiday = holiday_name(today)
    trading_day = is_trading_day(today)
    current_time = now_ist.time()

    if not trading_day:
        phase = SessionPhase.CLOSED
        is_open = False
        label = f"Market Closed — {today_holiday}" if today_holiday else "Market Closed — Weekend"
    elif current_time < PRE_OPEN_START:
        phase = SessionPhase.CLOSED
        is_open = False
        label = "Market Closed"
    elif current_time < MARKET_OPEN:
        phase = SessionPhase.PRE_OPEN
        is_open = False
        label = "Pre-Open Session"
    elif current_time <= MARKET_CLOSE:
        phase = SessionPhase.OPEN
        is_open = True
        label = "Market Open"
    elif current_time <= POST_CLOSE_END:
        phase = SessionPhase.POST_CLOSE
        is_open = False
        label = "Post-Close Session"
    else:
        phase = SessionPhase.CLOSED
        is_open = False
        label = "Market Closed"

    return MarketStatus(
        is_open=is_open,
        phase=phase,
        label=label,
        current_time_ist=now_ist,
        holiday_name=today_holiday,
        is_trading_day=trading_day,
        next_trading_day=today if (trading_day and phase in (SessionPhase.PRE_OPEN, SessionPhase.OPEN)) else next_trading_day(today),
        previous_trading_day=previous_trading_day(today) if not (trading_day and phase == SessionPhase.OPEN) else today,
        holiday_data_available=has_holiday_data(today.year),
    )
