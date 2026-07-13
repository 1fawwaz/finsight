"""Tests for core.metadata_registry: per-internal_id rollup metadata (spec §7.11)."""

from datetime import date

from core.database import MetadataRegistry, Price, SymbolRegistry
from core.metadata_registry import refresh_metadata
from core.validation import run_full_validation


def _add_registry_entry(session, internal_id="FIN-0001", symbol="RELIANCE.NS"):
    session.add(SymbolRegistry(internal_id=internal_id, current_symbol=symbol, historical_symbols_json="[]", rename_history_json="[]", merger_history_json="[]"))


def _add_price(session, internal_id, day, close=100.0, ticker_id=1):
    session.add(Price(ticker_id=ticker_id, internal_id=internal_id, date=day, open=close, high=close + 1, low=close - 1, close=close, volume=1_000_000))


def test_refresh_metadata_computes_first_last_date_and_row_count(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    _add_price(db_session, "FIN-0001", date(2024, 1, 2))
    _add_price(db_session, "FIN-0001", date(2024, 1, 3))
    db_session.flush()

    entry = refresh_metadata(db_session, "FIN-0001")

    assert entry.first_date == date(2024, 1, 1)
    assert entry.latest_date == date(2024, 1, 3)
    assert entry.row_count == 3


def test_refresh_metadata_sets_exchange_currency_timezone_provider(db_session):
    _add_registry_entry(db_session, symbol="RELIANCE.NS")
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    entry = refresh_metadata(db_session, "FIN-0001")

    assert entry.exchange == "NSE"
    assert entry.currency == "INR"
    assert entry.timezone == "Asia/Kolkata"
    assert entry.data_provider == "yfinance"


def test_refresh_metadata_bse_exchange(db_session):
    _add_registry_entry(db_session, symbol="SOMECO.BO")
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    entry = refresh_metadata(db_session, "FIN-0001")

    assert entry.exchange == "BSE"


def test_refresh_metadata_is_idempotent_single_row(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    refresh_metadata(db_session, "FIN-0001")
    refresh_metadata(db_session, "FIN-0001")

    assert db_session.query(MetadataRegistry).count() == 1


def test_refresh_metadata_reflects_new_rows_on_rerun(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()
    first = refresh_metadata(db_session, "FIN-0001")
    assert first.row_count == 1

    _add_price(db_session, "FIN-0001", date(2024, 1, 2))
    db_session.flush()
    second = refresh_metadata(db_session, "FIN-0001")

    assert second.row_count == 2
    assert second.latest_date == date(2024, 1, 2)


def test_refresh_metadata_validation_status_not_validated_before_any_run(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    entry = refresh_metadata(db_session, "FIN-0001")

    assert entry.validation_status == "not_validated"


def test_refresh_metadata_validation_status_reflects_latest_validation_run(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()
    run_full_validation(db_session, "FIN-0001")

    entry = refresh_metadata(db_session, "FIN-0001")

    assert entry.validation_status in ("passed", "failed")  # deterministic given the checks, but assert it's not the default


def test_refresh_metadata_checksum_changes_when_data_changes(db_session):
    """refresh_metadata mutates and returns the same MetadataRegistry instance on a
    second call (an upsert, not a new object) -- capture the checksum value itself
    right after each call, not a reference to the mutable row."""
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), close=100.0)
    db_session.flush()
    first_checksum = refresh_metadata(db_session, "FIN-0001").checksum

    _add_price(db_session, "FIN-0001", date(2024, 1, 2), close=105.0)
    db_session.flush()
    second_checksum = refresh_metadata(db_session, "FIN-0001").checksum

    assert first_checksum != second_checksum


def test_refresh_metadata_handles_no_price_rows(db_session):
    _add_registry_entry(db_session)

    entry = refresh_metadata(db_session, "FIN-0001")

    assert entry.row_count == 0
    assert entry.first_date is None
    assert entry.checksum is None


def test_refresh_metadata_accepts_optional_feature_and_dataset_version(db_session):
    _add_registry_entry(db_session)
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    entry = refresh_metadata(db_session, "FIN-0001", feature_version="features_v1", dataset_version="dataset_v1")

    assert entry.feature_version == "features_v1"
    assert entry.dataset_version == "dataset_v1"
