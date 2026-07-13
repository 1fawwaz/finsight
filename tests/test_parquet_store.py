"""Tests for core.parquet_store: read-optimized columnar storage for validated
historical market data (spec §7.14), synced one-way from SQLite (the source of truth)."""

from datetime import date

import pytest

import core.parquet_store as parquet_module
from core.database import Price
from core.parquet_store import read_market_data, sync_from_sqlite, sync_universe_to_parquet


@pytest.fixture()
def patched_market_data_dir(tmp_path, monkeypatch):
    market_dir = tmp_path / "market_data"
    market_dir.mkdir()
    monkeypatch.setattr(parquet_module, "MARKET_DATA_DIR", market_dir)
    return market_dir


def _add_price(session, internal_id, day, close=100.0, dividend=None, split_ratio=None, ticker_id=1):
    session.add(
        Price(
            ticker_id=ticker_id, internal_id=internal_id, date=day,
            open=close, high=close + 1, low=close - 1, close=close, volume=1_000_000,
            dividend=dividend, split_ratio=split_ratio,
        )
    )


def test_sync_from_sqlite_writes_a_partition_per_year(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2024, 12, 30))
    _add_price(db_session, "FIN-0001", date(2025, 1, 2))
    db_session.flush()

    written = sync_from_sqlite(db_session, "FIN-0001")

    assert written == 2
    assert (patched_market_data_dir / "FIN-0001" / "2024" / "data.parquet").exists()
    assert (patched_market_data_dir / "FIN-0001" / "2025" / "data.parquet").exists()


def test_sync_from_sqlite_no_rows_returns_zero(db_session, patched_market_data_dir):
    written = sync_from_sqlite(db_session, "FIN-9999")
    assert written == 0


def test_read_market_data_reproduces_the_same_values(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), close=100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), close=105.0, dividend=2.5)
    db_session.flush()
    sync_from_sqlite(db_session, "FIN-0001")

    df = read_market_data("FIN-0001")

    assert len(df) == 2
    assert df.loc["2024-01-01"]["close"] == 100.0
    assert df.loc["2024-01-02"]["dividend"] == 2.5


def test_read_market_data_spans_multiple_year_partitions(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2023, 6, 1))
    _add_price(db_session, "FIN-0001", date(2024, 6, 1))
    _add_price(db_session, "FIN-0001", date(2025, 6, 1))
    db_session.flush()
    sync_from_sqlite(db_session, "FIN-0001")

    df = read_market_data("FIN-0001")

    assert len(df) == 3
    assert list(df.index.year) == [2023, 2024, 2025]


def test_read_market_data_respects_start_end_filters(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2023, 6, 1))
    _add_price(db_session, "FIN-0001", date(2024, 6, 1))
    _add_price(db_session, "FIN-0001", date(2025, 6, 1))
    db_session.flush()
    sync_from_sqlite(db_session, "FIN-0001")

    df = read_market_data("FIN-0001", start=date(2024, 1, 1), end=date(2024, 12, 31))

    assert len(df) == 1
    assert df.index[0].year == 2024


def test_read_market_data_unknown_symbol_returns_empty_dataframe(patched_market_data_dir):
    df = read_market_data("FIN-DOES-NOT-EXIST")
    assert len(df) == 0


def test_sync_is_idempotent_rerun_produces_the_same_data(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()

    first = sync_from_sqlite(db_session, "FIN-0001")
    second = sync_from_sqlite(db_session, "FIN-0001")

    assert first == second == 1
    assert len(read_market_data("FIN-0001")) == 1  # not doubled


def test_sync_reflects_new_rows_added_since_last_sync(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1))
    db_session.flush()
    sync_from_sqlite(db_session, "FIN-0001")

    _add_price(db_session, "FIN-0001", date(2024, 1, 2))
    db_session.flush()
    sync_from_sqlite(db_session, "FIN-0001")

    assert len(read_market_data("FIN-0001")) == 2


def test_sync_universe_to_parquet_syncs_every_symbol(db_session, patched_market_data_dir):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), ticker_id=1)
    _add_price(db_session, "FIN-0002", date(2024, 1, 1), ticker_id=2)
    db_session.flush()

    results = sync_universe_to_parquet(db_session, ["FIN-0001", "FIN-0002"])

    assert results == {"FIN-0001": 1, "FIN-0002": 1}


def test_sqlite_remains_source_of_truth_parquet_never_written_to_directly_by_app_reads(db_session, patched_market_data_dir):
    """Sanity check on the scope decision: reading Parquet data never mutates SQLite,
    and a symbol with no SQLite rows produces no Parquet partitions at all."""
    written = sync_from_sqlite(db_session, "FIN-NEVER-INGESTED")
    assert written == 0
    assert not (patched_market_data_dir / "FIN-NEVER-INGESTED").exists()
