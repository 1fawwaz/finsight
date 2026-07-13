"""Tests for core.validation: the full spec §7.8 checklist, persisted to validation_log."""

from datetime import date

from core.database import Price, SymbolRegistry, ValidationLog
from core.validation import CHECK_NAMES, run_full_validation


def _add_price(session, internal_id, day, close=100.0, high=None, low=None, volume=1_000_000, ticker_id=1):
    session.add(
        Price(
            ticker_id=ticker_id,
            internal_id=internal_id,
            date=day,
            open=close,
            high=high if high is not None else close + 1,
            low=low if low is not None else close - 1,
            close=close,
            volume=volume,
        )
    )


def _add_registry_entry(session, internal_id="FIN-0001", symbol="RELIANCE.NS"):
    session.add(SymbolRegistry(internal_id=internal_id, current_symbol=symbol, historical_symbols_json="[]", rename_history_json="[]", merger_history_json="[]"))


# A real week of consecutive NSE trading days with no published holiday in that window
# (avoids flakiness from future/undocumented holiday years) -- Mon 2024-01-01 is itself
# a holiday-free ordinary week per the bundled calendar's absence for 2024, so
# is_trading_day degrades to weekday-only for it, which is fine for this test's purpose.
_TRADING_WEEK = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]


def test_run_full_validation_logs_every_check_name(db_session):
    _add_registry_entry(db_session)
    for d in _TRADING_WEEK:
        _add_price(db_session, "FIN-0001", d)
    db_session.flush()

    run_full_validation(db_session, "FIN-0001")

    logged_checks = {row.check_name for row in db_session.query(ValidationLog).all()}
    assert logged_checks == set(CHECK_NAMES)


def test_clean_data_passes_every_check(db_session):
    _add_registry_entry(db_session)
    for d in _TRADING_WEEK:
        _add_price(db_session, "FIN-0001", d)
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    assert report.all_passed, [r.check_name for r in report.results if not r.passed]


def test_ohlc_integrity_fails_on_high_below_low(db_session):
    _add_registry_entry(db_session)
    db_session.add(Price(ticker_id=1, internal_id="FIN-0001", date=date(2024, 1, 1), open=100, high=90, low=95, close=92, volume=1000))
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    assert report.result_for("ohlc_integrity").passed is False


def test_duplicate_row_check_fails_on_duplicate_dates(db_session):
    """The real UNIQUE(ticker_id, date) constraint prevents a true duplicate under one
    ticker_id -- the realistic path to an internal_id-level duplicate is two different
    Ticker rows (e.g. pre- and post-rename) both recording the same trading date under
    one internal_id, which nothing at the DB level currently prevents."""
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), ticker_id=1)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), ticker_id=2)  # different ticker_id, same date+internal_id
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    assert report.result_for("duplicate_row").passed is False


def test_missing_date_calendar_fails_when_a_trading_day_has_no_candle(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    _add_price(db_session, "FIN-0001", date(2024, 1, 3))  # skips Jan 2 (a Tuesday -- real trading day)
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    result = report.result_for("missing_date_calendar")
    assert result.passed is False
    assert "2024-01-02" in result.detail["missing_trading_dates"]


def test_calendar_consistency_fails_on_weekend_row(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 6))  # a Saturday -- not a trading day
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    result = report.result_for("calendar_consistency")
    assert result.passed is False
    assert "2024-01-06" in result.detail["non_trading_day_rows"]


def test_symbol_identity_fails_when_no_registry_entry_exists(db_session):
    # Deliberately skip _add_registry_entry -- no SymbolRegistry row for this internal_id.
    _add_price(db_session, "FIN-9999", date(2024, 1, 1))
    db_session.flush()

    report = run_full_validation(db_session, "FIN-9999")

    assert report.result_for("symbol_identity").passed is False


def test_volume_anomaly_fails_on_negative_volume(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), volume=-100)
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    assert report.result_for("volume_anomaly").passed is False


def test_corporate_action_consistency_fails_on_unexplained_move(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), close=100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), close=140.0)  # +40% -- both an outlier AND unexplained
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    assert report.result_for("corporate_action_consistency").passed is False


def test_adjusted_close_consistency_always_vacuously_passes_and_says_so(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    report = run_full_validation(db_session, "FIN-0001")

    result = report.result_for("adjusted_close_consistency")
    assert result.passed is True
    assert result.detail["status"] == "not_applicable"


def test_run_full_validation_handles_no_price_rows_gracefully(db_session):
    _add_registry_entry(db_session)
    # No Price rows at all for this internal_id.

    report = run_full_validation(db_session, "FIN-0001")

    # Must not crash; calendar checks vacuously pass with an explanatory detail.
    assert report.result_for("missing_date_calendar").passed is True
    assert report.result_for("calendar_consistency").passed is True
