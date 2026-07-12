"""Tests for core.data_ingestion: validation and idempotent upserts."""

from datetime import date

import pandas as pd
import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.data_ingestion import IngestionError, _validate_history, get_or_create_ticker, upsert_prices
from core.database import Price, Ticker


def _make_history(dates: list[str]) -> pd.DataFrame:
    index = pd.to_datetime(dates)
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(len(dates))],
            "High": [101.0 + i for i in range(len(dates))],
            "Low": [99.0 + i for i in range(len(dates))],
            "Close": [100.5 + i for i in range(len(dates))],
            "Volume": [1_000_000 + i for i in range(len(dates))],
        },
        index=index,
    )


def test_validate_history_rejects_empty():
    with pytest.raises(IngestionError):
        _validate_history("RELIANCE.NS", pd.DataFrame())


def test_validate_history_rejects_missing_columns():
    bad = pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2024-01-01"]))
    with pytest.raises(IngestionError):
        _validate_history("RELIANCE.NS", bad)


def test_validate_history_accepts_well_formed():
    good = _make_history(["2024-01-01", "2024-01-02"])
    _validate_history("RELIANCE.NS", good)  # should not raise


def test_get_or_create_ticker_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"info": {"shortName": "Reliance Industries Ltd.", "sector": "Energy"}})(),
    )

    first = get_or_create_ticker(db_session, "reliance.ns")
    db_session.commit()
    second = get_or_create_ticker(db_session, "RELIANCE.NS")

    assert first.id == second.id
    assert db_session.query(Ticker).count() == 1
    assert first.name == "Reliance Industries Ltd."


def test_get_or_create_ticker_rejects_unsupported_market(db_session):
    with pytest.raises(IngestionError, match="Only Indian stocks"):
        get_or_create_ticker(db_session, "AAPL")


def test_get_or_create_ticker_allows_benchmark_indices(db_session, monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"info": {}})(),
    )
    ticker = get_or_create_ticker(db_session, "^NSEI")
    assert ticker.symbol == "^NSEI"


def test_get_or_create_ticker_survives_a_race(db_session, monkeypatch):
    """A genuine race: another caller creates the Ticker row for this exact symbol after
    this call's own pre-check SELECT finds nothing, but before its own insert runs (e.g.
    while it's still waiting on the yfinance metadata call). The DB-level
    ON CONFLICT DO NOTHING must absorb that without raising IntegrityError, and the
    concurrent insert -- not this call's -- must win, so there is still exactly one row."""

    def _yfinance_call_races_a_concurrent_insert(symbol):
        db_session.execute(
            sqlite_insert(Ticker)
            .values(symbol="RELIANCE.NS", name="Inserted By Concurrent Caller", sector="Energy")
            .on_conflict_do_nothing(index_elements=["symbol"])
        )
        db_session.flush()
        return type("T", (), {"info": {"shortName": "This Call's Own Name", "sector": "Energy"}})()

    monkeypatch.setattr("core.data_ingestion.yf.Ticker", _yfinance_call_races_a_concurrent_insert)

    ticker = get_or_create_ticker(db_session, "reliance.ns")  # must not raise IntegrityError

    assert ticker.name == "Inserted By Concurrent Caller"
    assert db_session.query(Ticker).filter(Ticker.symbol == "RELIANCE.NS").count() == 1


def test_upsert_prices_inserts_new_rows(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(["2024-01-01", "2024-01-02", "2024-01-03"])
    inserted = upsert_prices(db_session, ticker, history)
    db_session.commit()

    assert inserted == 3
    assert db_session.query(Price).count() == 3


def test_upsert_prices_is_idempotent_on_rerun(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(["2024-01-01", "2024-01-02", "2024-01-03"])
    first_run = upsert_prices(db_session, ticker, history)
    db_session.commit()

    second_run = upsert_prices(db_session, ticker, history)
    db_session.commit()

    assert first_run == 3
    assert second_run == 0
    assert db_session.query(Price).count() == 3


def test_upsert_prices_skips_rows_with_nan(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(["2024-01-01", "2024-01-02"])
    history.loc[history.index[0], "Close"] = float("nan")

    inserted = upsert_prices(db_session, ticker, history)

    assert inserted == 1
    assert db_session.query(Price).filter(Price.date == date(2024, 1, 2)).one() is not None
