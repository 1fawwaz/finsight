"""Tests for core.market_status: NSE session status, holiday awareness, next/previous trading day."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.market_status import (
    SessionPhase,
    get_nse_market_status,
    has_holiday_data,
    holiday_name,
    is_trading_day,
    next_trading_day,
    prediction_target_session,
    previous_trading_day,
)

IST = ZoneInfo("Asia/Kolkata")


def test_holiday_name_known_holiday():
    assert holiday_name(date(2026, 1, 26)) == "Republic Day"


def test_holiday_name_ordinary_weekday_is_none():
    assert holiday_name(date(2026, 1, 27)) is None


def test_is_trading_day_false_on_weekend():
    saturday = date(2026, 1, 24)
    sunday = date(2026, 1, 25)
    assert not is_trading_day(saturday)
    assert not is_trading_day(sunday)


def test_is_trading_day_false_on_holiday():
    assert not is_trading_day(date(2026, 1, 26))  # Republic Day, a Monday


def test_is_trading_day_true_on_ordinary_weekday():
    assert is_trading_day(date(2026, 1, 27))


def test_next_trading_day_skips_weekend():
    friday = date(2026, 1, 23)
    # Sat 24 / Sun 25 skipped, Mon 26 is Republic Day (also a holiday) -> Tue 27 Jan
    assert next_trading_day(friday) == date(2026, 1, 27)


def test_next_trading_day_skips_holiday_and_weekend():
    sunday = date(2026, 3, 1)  # Sunday
    # Mon 2 Mar ordinary trading day
    assert next_trading_day(sunday) == date(2026, 3, 2)


def test_previous_trading_day_skips_weekend():
    monday = date(2026, 1, 27)
    # Sun 25, Sat 24 skipped -> Fri 23 Jan is a trading day
    assert previous_trading_day(monday) == date(2026, 1, 23)


def test_prediction_target_session_sunday_targets_next_open_day():
    sunday_noon = datetime(2026, 3, 1, 12, 0, tzinfo=IST)
    assert prediction_target_session(sunday_noon) == date(2026, 3, 2)


def test_prediction_target_session_before_holiday_skips_it():
    sunday_before_republic_day = datetime(2026, 1, 25, 12, 0, tzinfo=IST)
    assert prediction_target_session(sunday_before_republic_day) == date(2026, 1, 27)


def test_has_holiday_data_known_year():
    assert has_holiday_data(2026)


def test_has_holiday_data_unknown_year():
    assert not has_holiday_data(2099)


def test_get_nse_market_status_open_during_trading_hours():
    weekday_open = datetime(2026, 1, 27, 11, 0, tzinfo=IST)  # ordinary Tuesday
    status = get_nse_market_status(weekday_open)
    assert status.is_open is True
    assert status.phase == SessionPhase.OPEN
    assert status.label == "Market Open"
    assert status.holiday_name is None
    assert status.is_trading_day is True


def test_get_nse_market_status_pre_open():
    pre_open = datetime(2026, 1, 27, 9, 5, tzinfo=IST)
    status = get_nse_market_status(pre_open)
    assert status.is_open is False
    assert status.phase == SessionPhase.PRE_OPEN


def test_get_nse_market_status_post_close():
    post_close = datetime(2026, 1, 27, 15, 45, tzinfo=IST)
    status = get_nse_market_status(post_close)
    assert status.is_open is False
    assert status.phase == SessionPhase.POST_CLOSE


def test_get_nse_market_status_weekend_labels_weekend():
    saturday = datetime(2026, 1, 24, 11, 0, tzinfo=IST)
    status = get_nse_market_status(saturday)
    assert status.is_open is False
    assert status.phase == SessionPhase.CLOSED
    assert "Weekend" in status.label
    assert status.is_trading_day is False


def test_get_nse_market_status_holiday_names_the_holiday():
    republic_day = datetime(2026, 1, 26, 11, 0, tzinfo=IST)
    status = get_nse_market_status(republic_day)
    assert status.is_open is False
    assert status.holiday_name == "Republic Day"
    assert "Republic Day" in status.label


def test_get_nse_market_status_next_trading_day_when_currently_open():
    weekday_open = datetime(2026, 1, 27, 11, 0, tzinfo=IST)
    status = get_nse_market_status(weekday_open)
    assert status.next_trading_day == date(2026, 1, 27)


def test_get_nse_market_status_next_trading_day_when_closed():
    saturday = datetime(2026, 1, 24, 11, 0, tzinfo=IST)
    status = get_nse_market_status(saturday)
    # Sat 24 -> Sun 25 -> Mon 26 is Republic Day (holiday) -> Tue 27 is next trading day
    assert status.next_trading_day == date(2026, 1, 27)
