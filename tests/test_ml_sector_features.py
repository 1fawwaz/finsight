"""Tests for core.ml.sector_features: Phase 2 Step 3 (Sector-Relative Features).
Sector membership is always read from core.database.Ticker.sector -- never hardcoded."""

import numpy as np
import pandas as pd

from core.database import Price, Ticker, get_session
from core.ml.sector_features import build_sector_composite, build_sector_relative_features, get_sector_peers


def _seed_ticker(session, symbol: str, sector: str | None, closes: list[float], start: str = "2023-01-02"):
    ticker = Ticker(symbol=symbol, name=symbol, sector=sector)
    session.add(ticker)
    session.flush()
    dates = pd.bdate_range(start, periods=len(closes))
    for d, c in zip(dates, closes):
        session.add(Price(ticker_id=ticker.id, date=d.date(), open=c, high=c + 1, low=c - 1, close=c, volume=1_000_000))
    session.flush()
    return ticker


def _flat_walk(n: int, start: float = 100.0, drift: float = 0.0, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    values = [start]
    for _ in range(n - 1):
        values.append(values[-1] * (1 + drift + rng.normal(0, 0.005)))
    return values


def test_get_sector_peers_returns_other_tickers_same_sector(temp_db):
    with get_session() as session:
        _seed_ticker(session, "AAA.NS", "Technology", _flat_walk(30))
        _seed_ticker(session, "BBB.NS", "Technology", _flat_walk(30, seed=1))
        _seed_ticker(session, "CCC.NS", "Energy", _flat_walk(30, seed=2))

    with get_session() as session:
        peers = get_sector_peers(session, "AAA.NS")

    assert peers == ["BBB.NS"]


def test_get_sector_peers_empty_when_no_sector(temp_db):
    with get_session() as session:
        _seed_ticker(session, "NOSECTOR.NS", None, _flat_walk(30))

    with get_session() as session:
        peers = get_sector_peers(session, "NOSECTOR.NS")

    assert peers == []


def test_get_sector_peers_empty_when_ticker_not_found(temp_db):
    with get_session() as session:
        peers = get_sector_peers(session, "DOESNOTEXIST.NS")
    assert peers == []


def test_build_sector_relative_features_nan_when_insufficient_peers(temp_db):
    with get_session() as session:
        _seed_ticker(session, "ONLYONE.NS", "Basic Materials", _flat_walk(30))

    with get_session() as session:
        history = session.query(Price).filter(Price.ticker_id == session.query(Ticker).filter_by(symbol="ONLYONE.NS").one().id).all()
        price_df = pd.DataFrame(
            [{"date": r.date, "close": r.close} for r in history]
        ).assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")

        features = build_sector_relative_features(session, "ONLYONE.NS", price_df)

    assert features["relative_strength_vs_sector"].isna().all()
    assert features["sector_breadth"].isna().all()


def test_build_sector_relative_features_computes_real_values_with_peers(temp_db):
    with get_session() as session:
        outperformer = _seed_ticker(session, "WINNER.NS", "Technology", _flat_walk(60, drift=0.01, seed=10))
        _seed_ticker(session, "LAGGARD1.NS", "Technology", _flat_walk(60, drift=0.0, seed=11))
        _seed_ticker(session, "LAGGARD2.NS", "Technology", _flat_walk(60, drift=0.0, seed=12))

    with get_session() as session:
        history = session.query(Price).filter(Price.ticker_id == outperformer.id).order_by(Price.date).all()
        price_df = pd.DataFrame(
            [{"date": r.date, "close": r.close} for r in history]
        ).assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")

        features = build_sector_relative_features(session, "WINNER.NS", price_df)

    valid = features.dropna()
    assert len(valid) > 0
    # WINNER.NS has a strong positive drift the other two don't -- relative strength
    # should clearly exceed 1 (outperforming its sector composite) by the end.
    assert valid["relative_strength_vs_sector"].iloc[-1] > 1.0
    assert (valid["sector_breadth"] >= 0.0).all() and (valid["sector_breadth"] <= 1.0).all()


def test_excess_return_matches_hand_computed_difference(temp_db):
    """MIN_SECTOR_PEERS=2, so at least two peers are required for the feature to
    compute at all (matching real behavior -- e.g. BHARTIARTL.NS is the only
    Communication Services stock in the real tracked universe and correctly gets NaN)."""
    with get_session() as session:
        stock = _seed_ticker(session, "STOCK.NS", "Energy", [100, 102, 101, 105])
        _seed_ticker(session, "PEER1.NS", "Energy", [50, 50.5, 50.2, 51])
        _seed_ticker(session, "PEER2.NS", "Energy", [80, 79.5, 81, 82])

    with get_session() as session:
        history = session.query(Price).filter(Price.ticker_id == stock.id).order_by(Price.date).all()
        price_df = pd.DataFrame(
            [{"date": r.date, "close": r.close} for r in history]
        ).assign(date=lambda d: pd.to_datetime(d["date"])).set_index("date")

        features = build_sector_relative_features(session, "STOCK.NS", price_df)

        stock_returns = price_df["close"].pct_change()
        from core.ml.sector_features import get_sector_peers
        peer_returns = build_sector_composite(get_sector_peers(session, "STOCK.NS"))

    expected_excess = (stock_returns - peer_returns.reindex(price_df.index)).dropna()
    actual_excess = features["excess_return_vs_sector"].dropna()
    pd.testing.assert_series_equal(actual_excess, expected_excess, check_names=False)
