"""Tests for core.queries read helpers, against a throwaway DB."""

from datetime import date, timedelta

from core.database import Price, Ticker, get_session
from core.queries import get_multi_symbol_close, get_price_history, get_ticker_info, list_ticker_symbols


def _seed(symbol: str, name: str, sector: str, closes: list[float]) -> None:
    with get_session() as session:
        ticker = Ticker(symbol=symbol, name=name, sector=sector)
        session.add(ticker)
        session.flush()
        start = date(2024, 1, 1)
        for i, close in enumerate(closes):
            session.add(
                Price(
                    ticker_id=ticker.id,
                    date=start + timedelta(days=i),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1_000_000,
                )
            )


def test_list_ticker_symbols_sorted(temp_db):
    _seed("TCS.NS", "Tata Consultancy Services", "Technology", [100.0])
    _seed("RELIANCE.NS", "Reliance Industries Ltd.", "Energy", [100.0])

    assert list_ticker_symbols() == ["RELIANCE.NS", "TCS.NS"]


def test_get_ticker_info_found_and_missing(temp_db):
    _seed("RELIANCE.NS", "Reliance Industries Ltd.", "Energy", [100.0])

    info = get_ticker_info("reliance.ns")
    assert info == {"symbol": "RELIANCE.NS", "name": "Reliance Industries Ltd.", "sector": "Energy"}
    assert get_ticker_info("TCS.NS") is None


def test_get_price_history_shape_and_missing_symbol(temp_db):
    _seed("RELIANCE.NS", "Reliance Industries Ltd.", "Energy", [100.0, 101.0, 99.0])

    history = get_price_history("RELIANCE.NS")
    assert list(history.columns) == ["open", "high", "low", "close", "volume"]
    assert len(history) == 3
    assert history["close"].tolist() == [100.0, 101.0, 99.0]

    assert get_price_history("NOPE.NS").empty


def test_get_multi_symbol_close_inner_joins_on_shared_dates(temp_db):
    _seed("RELIANCE.NS", "Reliance Industries Ltd.", "Energy", [100.0, 101.0, 99.0])
    _seed("TCS.NS", "Tata Consultancy Services", "Technology", [200.0, 202.0])  # one day shorter

    combined = get_multi_symbol_close(["RELIANCE.NS", "TCS.NS"])

    assert list(combined.columns) == ["RELIANCE.NS", "TCS.NS"]
    assert len(combined) == 2  # only the overlapping two days survive the inner join
