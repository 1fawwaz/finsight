"""Tests for core.data_ingestion: validation and idempotent upserts."""

from datetime import date

import pandas as pd
import pytest
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.data_ingestion import IngestionError, _validate_history, fetch_price_history, get_or_create_ticker, upsert_prices
from core.database import Price, ProviderHealth, Ticker


def _make_history(dates: list[str], dividends: list[float] | None = None, splits: list[float] | None = None) -> pd.DataFrame:
    index = pd.to_datetime(dates)
    data = {
        "Open": [100.0 + i for i in range(len(dates))],
        "High": [101.0 + i for i in range(len(dates))],
        "Low": [99.0 + i for i in range(len(dates))],
        "Close": [100.5 + i for i in range(len(dates))],
        "Volume": [1_000_000 + i for i in range(len(dates))],
    }
    if dividends is not None:
        data["Dividends"] = dividends
    if splits is not None:
        data["Stock Splits"] = splits
    return pd.DataFrame(data, index=index)


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


def test_upsert_prices_stamps_internal_id_when_provided(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(["2024-01-01"])
    upsert_prices(db_session, ticker, history, internal_id="FIN-0001")

    row = db_session.query(Price).one()
    assert row.internal_id == "FIN-0001"


def test_upsert_prices_without_internal_id_is_unchanged_from_before_phase1(db_session):
    """Backward-compatibility guarantee: existing callers that don't pass internal_id
    (e.g. any pre-Phase-1 code path) see byte-identical behavior -- internal_id stays
    NULL, dedup is still purely by (ticker_id, date)."""
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(["2024-01-01"])
    inserted = upsert_prices(db_session, ticker, history)

    assert inserted == 1
    row = db_session.query(Price).one()
    assert row.internal_id is None


def test_upsert_prices_dedups_across_ticker_rows_sharing_the_same_internal_id(db_session):
    """The actual identity-safety guarantee spec §7.3 asks for: two different Ticker
    rows (e.g. an old-symbol row and a post-rename new-symbol row) sharing one
    internal_id must not produce a duplicate price row for the same trading date."""
    old_ticker = Ticker(symbol="OLDSYM.NS")
    new_ticker = Ticker(symbol="NEWSYM.NS")
    db_session.add_all([old_ticker, new_ticker])
    db_session.flush()

    history = _make_history(["2024-01-01", "2024-01-02"])
    first = upsert_prices(db_session, old_ticker, history, internal_id="FIN-0001")
    # Same trading dates arrive again, but now attributed to the post-rename Ticker row --
    # a real scenario if a rename happens mid-stream and a caller re-resolves the symbol.
    second = upsert_prices(db_session, new_ticker, history, internal_id="FIN-0001")

    assert first == 2
    assert second == 0  # already covered under the same internal_id, via a different ticker_id
    assert db_session.query(Price).count() == 2


def test_fetch_price_history_records_provider_health_on_success(temp_db, monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"history": lambda self, period="5y", auto_adjust=False: _make_history(["2024-01-01"])})(),
    )

    fetch_price_history("RELIANCE.NS")

    from core.database import get_session

    with get_session() as session:
        row = session.query(ProviderHealth).one()
        assert row.provider == "yfinance"
        assert row.success is True
        assert row.latency_ms is not None


def test_fetch_price_history_records_provider_health_on_failure_and_still_raises(temp_db, monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"history": lambda self, period="5y", auto_adjust=False: pd.DataFrame()})(),
    )

    with pytest.raises(IngestionError):
        fetch_price_history("RELIANCE.NS")

    from core.database import get_session

    with get_session() as session:
        row = session.query(ProviderHealth).one()
        assert row.success is False
        assert row.failure_type == "malformed_response"


def test_fetch_price_history_wraps_raw_network_errors_as_ingestion_error(temp_db, monkeypatch):
    """Regression test for a real bug found during Production Stabilization Phase 6:
    a genuine network interruption (ConnectionError, timeout, DNS failure) from
    yfinance's own `.history()` call used to propagate as its own raw exception type
    instead of `IngestionError`. Every UI call site (`pages/*.py`'s `_ensure_ingested`
    helpers) only catches `except IngestionError` to show a friendly warning -- a raw
    network exception bypassed that entirely and surfaced as an unhandled exception.
    Reproduced live with a simulated `requests.exceptions.ConnectionError` before this
    fix; this test locks in the fix."""
    import requests

    def _raise_connection_error(self, period="5y", auto_adjust=False):
        raise requests.exceptions.ConnectionError("Simulated network interruption")

    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"history": _raise_connection_error})(),
    )

    with pytest.raises(IngestionError) as exc_info:
        fetch_price_history("RELIANCE.NS")

    assert isinstance(exc_info.value.__cause__, requests.exceptions.ConnectionError)

    from core.database import get_session

    with get_session() as session:
        row = session.query(ProviderHealth).one()
        assert row.success is False
        assert row.failure_type == "connection_error"


# --- Ingestion boundary contract: every expected provider/network failure normalizes
# to IngestionError, chains the original exception, and emits a structured log entry.
# (Production Reliability Fix -- Network Exception Normalization.) --------------------


def _raising_ticker(exc: Exception):
    """A fake yf.Ticker whose .history() always raises `exc`."""

    def _raise(self, period="5y", auto_adjust=False):
        raise exc

    return lambda symbol: type("T", (), {"history": _raise})()


@pytest.mark.parametrize(
    "scenario, exc_factory",
    [
        ("ConnectionError", lambda: __import__("requests").exceptions.ConnectionError("connection refused")),
        ("Timeout", lambda: __import__("requests").exceptions.Timeout("request timed out")),
        (
            "DNSFailure",
            lambda: __import__("requests").exceptions.ConnectionError(
                "HTTPSConnectionPool: Failed to resolve 'query1.finance.yahoo.com' ([Errno 11001] getaddrinfo failed)"
            ),
        ),
        ("HTTP500", lambda: __import__("requests").exceptions.HTTPError("500 Server Error: Internal Server Error")),
        (
            "HTTP429_YFRateLimit",
            lambda: __import__("yfinance.exceptions", fromlist=["YFRateLimitError"]).YFRateLimitError(),
        ),
        ("SSLError", lambda: __import__("requests").exceptions.SSLError("certificate verify failed")),
        (
            "ProviderUnavailable",
            lambda: __import__("yfinance.exceptions", fromlist=["YFTickerMissingError"]).YFTickerMissingError(
                "FAKE.NS", "no timezone found, symbol may be delisted"
            ),
        ),
    ],
)
def test_fetch_price_history_normalizes_every_expected_provider_failure(temp_db, monkeypatch, caplog, scenario, exc_factory):
    """For every expected provider/network failure type: the raw exception must NOT
    propagate, IngestionError MUST be raised, the original exception MUST be chained
    (`__cause__`), and a structured log entry MUST be emitted (Production Reliability
    Fix -- Network Exception Normalization, requirement #5)."""
    import logging

    original_exc = exc_factory()
    monkeypatch.setattr("core.data_ingestion.yf.Ticker", _raising_ticker(original_exc))

    with caplog.at_level(logging.ERROR, logger="core.data_ingestion"):
        with pytest.raises(IngestionError) as exc_info:
            fetch_price_history("RELIANCE.NS")

    # Raw exception NOT raised; IngestionError IS raised.
    assert isinstance(exc_info.value, IngestionError)
    # Original exception chained, not discarded.
    assert exc_info.value.__cause__ is original_exc
    # Structured log entry emitted for this failure.
    assert any("Market data ingestion failed" in rec.message for rec in caplog.records), (
        f"[{scenario}] expected a 'Market data ingestion failed' log entry, got: {[r.message for r in caplog.records]}"
    )


def test_fetch_price_history_empty_dataframe_still_raises_ingestion_error(temp_db, monkeypatch):
    """Invalid ticker / empty response scenario -- already the right exception type
    via _validate_history, re-asserted here alongside the other normalization tests
    for a complete before/after picture in one place."""
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"history": lambda self, period="5y", auto_adjust=False: pd.DataFrame()})(),
    )
    with pytest.raises(IngestionError):
        fetch_price_history("FAKE.NS")


def test_fetch_price_history_success_path_unaffected(temp_db, monkeypatch):
    """The success path (the other half of the ingestion boundary's contract) must be
    completely unaffected by the narrowed exception handling."""
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type("T", (), {"history": lambda self, period="5y", auto_adjust=False: _make_history(["2024-01-01"])})(),
    )
    result = fetch_price_history("RELIANCE.NS")
    assert not result.empty


def test_fetch_price_history_lets_real_programming_bugs_propagate_unmodified(temp_db, monkeypatch):
    """A genuine programming bug (not a provider/network failure) must propagate as
    its own exception type, not be masked as IngestionError -- per requirement #2
    ("unexpected programming bugs should continue propagating naturally")."""

    def _raise_type_error(self, period="5y", auto_adjust=False):
        raise TypeError("simulated bug in our own code, not a provider failure")

    monkeypatch.setattr("core.data_ingestion.yf.Ticker", lambda symbol: type("T", (), {"history": _raise_type_error})())

    with pytest.raises(TypeError):
        fetch_price_history("RELIANCE.NS")


def test_upsert_prices_captures_dividends_and_splits_when_present(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(
        ["2024-01-01", "2024-01-02", "2024-01-03"],
        dividends=[0.0, 5.5, 0.0],
        splits=[0.0, 0.0, 2.0],
    )
    upsert_prices(db_session, ticker, history)

    rows = {r.date: r for r in db_session.query(Price).all()}
    assert rows[date(2024, 1, 1)].dividend is None
    assert rows[date(2024, 1, 1)].split_ratio is None
    assert rows[date(2024, 1, 2)].dividend == 5.5
    assert rows[date(2024, 1, 2)].split_ratio is None
    assert rows[date(2024, 1, 3)].split_ratio == 2.0


def test_upsert_prices_without_dividends_splits_columns_still_works(db_session):
    """Backward compatible: a caller-supplied DataFrame without Dividends/Stock Splits
    columns (e.g. existing tests, or a manually constructed frame) must not crash."""
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    history = _make_history(["2024-01-01"])  # no dividends/splits columns at all
    inserted = upsert_prices(db_session, ticker, history)

    assert inserted == 1
    row = db_session.query(Price).one()
    assert row.dividend is None
    assert row.split_ratio is None


def test_ingest_ticker_stamps_internal_id_automatically(temp_db, monkeypatch):
    """Uses the temp_db fixture (patches core.database.SessionLocal) rather than the
    bare db_session fixture, since ingest_ticker opens its own session(s) internally --
    same pattern as tests/test_historical_backfill.py."""
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: type(
            "T",
            (),
            {
                "info": {"shortName": "Reliance Industries Ltd.", "sector": "Energy"},
                "history": lambda self, period="5y", auto_adjust=False: _make_history(["2024-01-01", "2024-01-02"]),
            },
        )(),
    )

    from core.data_ingestion import ingest_ticker
    from core.database import get_session

    inserted = ingest_ticker("RELIANCE.NS")

    assert inserted == 2
    with get_session() as session:
        rows = session.query(Price).all()
        assert len(rows) == 2
        assert all(r.internal_id is not None and r.internal_id.startswith("FIN-") for r in rows)
