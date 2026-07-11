"""Tests for core.database: schema creation and the unique price constraint."""

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from core.database import NewsSentiment, Price, Ticker


def test_ticker_symbol_unique(db_session):
    db_session.add(Ticker(symbol="RELIANCE.NS"))
    db_session.commit()
    db_session.add(Ticker(symbol="RELIANCE.NS"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_price_unique_ticker_date_constraint(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    db_session.add(
        Price(ticker_id=ticker.id, date=date(2024, 1, 1), open=1, high=2, low=0.5, close=1.5, volume=100)
    )
    db_session.commit()

    db_session.add(
        Price(ticker_id=ticker.id, date=date(2024, 1, 1), open=1, high=2, low=0.5, close=1.5, volume=100)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_news_sentiment_unique_ticker_headline_constraint(db_session):
    ticker = Ticker(symbol="RELIANCE.NS")
    db_session.add(ticker)
    db_session.flush()

    db_session.add(
        NewsSentiment(
            ticker_id=ticker.id,
            date=date(2024, 1, 1),
            headline="Company beats estimates",
            sentiment=0.5,
            confidence=0.8,
        )
    )
    db_session.commit()

    db_session.add(
        NewsSentiment(
            ticker_id=ticker.id,
            date=date(2024, 1, 2),  # different date, same headline -- still a duplicate article
            headline="Company beats estimates",
            sentiment=0.5,
            confidence=0.8,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
