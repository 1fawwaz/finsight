"""Tests for core.symbol_registry.backfill_price_internal_ids: the retroactive
migration that stamps internal_id onto Price rows ingested before the Symbol Registry
existed (spec §7.7's "this applies retroactively to any existing data keyed by raw
symbol, which must be backfilled with internal_id mappings, not left as-is")."""

from datetime import date

from core.database import Price, Ticker
from core.symbol_registry import backfill_price_internal_ids, backfill_registry_from_tickers, get_or_create


def test_backfill_stamps_internal_id_onto_existing_unstamped_rows(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()
    db_session.add(Price(ticker_id=ticker.id, date=date(2024, 1, 1), open=1, high=1, low=1, close=1, volume=1))
    db_session.flush()

    registry_entry = get_or_create(db_session, "RELIANCE.NS")

    updated = backfill_price_internal_ids(db_session)

    assert updated == 1
    row = db_session.query(Price).one()
    assert row.internal_id == registry_entry.internal_id


def test_backfill_is_idempotent_only_touches_unstamped_rows(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()
    db_session.add(Price(ticker_id=ticker.id, date=date(2024, 1, 1), open=1, high=1, low=1, close=1, volume=1))
    db_session.flush()
    get_or_create(db_session, "RELIANCE.NS")

    first = backfill_price_internal_ids(db_session)
    second = backfill_price_internal_ids(db_session)

    assert first == 1
    assert second == 0  # nothing left unstamped


def test_backfill_full_pipeline_from_tickers_through_prices(db_session):
    """The realistic end-to-end sequence: registry backfill first, then price backfill --
    matching the order this must run in against the real DB (registry entries must exist
    before prices can be joined to them)."""
    reliance = Ticker(symbol="RELIANCE.NS")
    tcs = Ticker(symbol="TCS.NS")
    db_session.add_all([reliance, tcs])
    db_session.flush()
    db_session.add_all(
        [
            Price(ticker_id=reliance.id, date=date(2024, 1, 1), open=1, high=1, low=1, close=1, volume=1),
            Price(ticker_id=reliance.id, date=date(2024, 1, 2), open=1, high=1, low=1, close=1, volume=1),
            Price(ticker_id=tcs.id, date=date(2024, 1, 1), open=1, high=1, low=1, close=1, volume=1),
        ]
    )
    db_session.flush()

    registry_result = backfill_registry_from_tickers(db_session)
    price_result = backfill_price_internal_ids(db_session)

    assert registry_result.created == 2
    assert price_result == 3
    assert all(row.internal_id is not None for row in db_session.query(Price).all())


def test_backfill_skips_rows_whose_ticker_has_no_registry_entry_yet(db_session):
    """Doesn't crash or silently guess -- a row for a ticker not yet in the registry is
    left unstamped and logged, not assigned a wrong/placeholder internal_id."""
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()
    db_session.add(Price(ticker_id=ticker.id, date=date(2024, 1, 1), open=1, high=1, low=1, close=1, volume=1))
    db_session.flush()
    # Deliberately do NOT call backfill_registry_from_tickers / get_or_create first.

    updated = backfill_price_internal_ids(db_session)

    assert updated == 0
    row = db_session.query(Price).one()
    assert row.internal_id is None
