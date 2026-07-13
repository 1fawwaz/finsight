"""Tests for core.ml.market_breadth: Phase 2 Step 4 (Market Breadth)."""

from datetime import date

import pandas as pd

from core.database import MarketBreadthDaily, Price, Ticker, get_session
from core.ml.market_breadth import compute_market_breadth, get_market_breadth, persist_market_breadth


def _seed_ticker(session, symbol: str, closes: list[float], start: str = "2023-01-02"):
    ticker = Ticker(symbol=symbol, name=symbol, sector="Technology")
    session.add(ticker)
    session.flush()
    dates = pd.bdate_range(start, periods=len(closes))
    for d, c in zip(dates, closes):
        session.add(Price(ticker_id=ticker.id, date=d.date(), open=c, high=c + 0.5, low=c - 0.5, close=c, volume=1_000_000))
    session.flush()


def test_compute_market_breadth_advance_decline_ratio(temp_db):
    with get_session() as session:
        _seed_ticker(session, "A.NS", [100, 101])  # advances
        _seed_ticker(session, "B.NS", [50, 51])    # advances
        _seed_ticker(session, "C.NS", [80, 79])    # declines

    breadth = compute_market_breadth(["A.NS", "B.NS", "C.NS"])

    last_row = breadth.iloc[-1]
    assert last_row["advance_decline_ratio"] == 2.0  # 2 advances / 1 decline


def test_compute_market_breadth_no_divide_by_zero_when_all_advance(temp_db):
    with get_session() as session:
        _seed_ticker(session, "A.NS", [100, 101])
        _seed_ticker(session, "B.NS", [50, 51])

    breadth = compute_market_breadth(["A.NS", "B.NS"])

    # 0 declines -- must not raise ZeroDivisionError or produce +/-inf; the correct,
    # intentional result is a missing value (pd.NA/NaN), not a sentinel infinity.
    last_row = breadth.iloc[-1]
    value = last_row["advance_decline_ratio"]
    assert pd.isna(value) or (value not in (float("inf"), float("-inf")))


def test_compute_market_breadth_empty_symbols_returns_empty_dataframe(temp_db):
    breadth = compute_market_breadth(["NEVER_INGESTED.NS"])
    assert breadth.empty


def test_pct_above_ema_and_participation_bounded_0_to_1(temp_db):
    with get_session() as session:
        _seed_ticker(session, "A.NS", [100 + i * 0.3 for i in range(40)])
        _seed_ticker(session, "B.NS", [50 - i * 0.1 for i in range(40)])

    breadth = compute_market_breadth(["A.NS", "B.NS"])
    valid = breadth.dropna(subset=["pct_above_ema20"])

    assert (valid["pct_above_ema20"] >= 0.0).all() and (valid["pct_above_ema20"] <= 1.0).all()
    assert (breadth["market_participation"].dropna() >= 0.0).all()
    assert (breadth["market_participation"].dropna() <= 1.0).all()


def test_new_highs_detected_on_a_real_breakout_day(temp_db):
    # 25 flat days then a clear breakout on the last day -- a real new 20-window high.
    closes = [100.0] * 25 + [150.0]
    with get_session() as session:
        _seed_ticker(session, "A.NS", closes)

    breadth = compute_market_breadth(["A.NS"])
    assert breadth.iloc[-1]["new_highs"] == 1


def test_persist_market_breadth_writes_rows(temp_db):
    with get_session() as session:
        _seed_ticker(session, "A.NS", [100, 101, 102])
        _seed_ticker(session, "B.NS", [50, 49, 51])

    with get_session() as session:
        written = persist_market_breadth(session, ["A.NS", "B.NS"])

    assert written == 3
    with get_session() as session:
        assert session.query(MarketBreadthDaily).count() == 3


def test_persist_market_breadth_is_idempotent_upsert_not_duplicate(temp_db):
    with get_session() as session:
        _seed_ticker(session, "A.NS", [100, 101, 102])
        _seed_ticker(session, "B.NS", [50, 49, 51])

    with get_session() as session:
        persist_market_breadth(session, ["A.NS", "B.NS"])
        persist_market_breadth(session, ["A.NS", "B.NS"])  # rerun

    with get_session() as session:
        assert session.query(MarketBreadthDaily).count() == 3  # not doubled


def test_get_market_breadth_respects_date_filters(temp_db):
    with get_session() as session:
        _seed_ticker(session, "A.NS", [100, 101, 102, 103, 104], start="2023-01-02")
        _seed_ticker(session, "B.NS", [50, 49, 51, 52, 53], start="2023-01-02")

    with get_session() as session:
        persist_market_breadth(session, ["A.NS", "B.NS"])
        all_rows = get_market_breadth(session)
        filtered = get_market_breadth(session, start=date(2023, 1, 4))

    assert len(filtered) < len(all_rows)
    assert filtered.index.min().date() >= date(2023, 1, 4)
