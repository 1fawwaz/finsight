"""SQLAlchemy ORM models and session management for FinSight.

The connection string lives in core.config.DATABASE_URL. Swapping SQLite for
PostgreSQL only requires changing that connection string (e.g. via the
DATABASE_URL environment variable) -- no model or query code needs to change.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from core.config import DATABASE_URL, get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all FinSight ORM models."""


class Ticker(Base):
    """A tradable symbol, e.g. RELIANCE.NS."""

    __tablename__ = "tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    prices: Mapped[list["Price"]] = relationship(back_populates="ticker", cascade="all, delete-orphan")
    news_sentiments: Mapped[list["NewsSentiment"]] = relationship(back_populates="ticker", cascade="all, delete-orphan")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="ticker", cascade="all, delete-orphan")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="ticker", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"Ticker(symbol={self.symbol!r})"


class Price(Base):
    """A single day's OHLCV bar for a ticker."""

    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_price_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)

    ticker: Mapped["Ticker"] = relationship(back_populates="prices")

    def __repr__(self) -> str:
        return f"Price(ticker_id={self.ticker_id}, date={self.date}, close={self.close})"


class NewsSentiment(Base):
    """AI or rule-based sentiment score for a news headline about a ticker."""

    __tablename__ = "news_sentiment"
    __table_args__ = (UniqueConstraint("ticker_id", "headline", name="uq_news_ticker_headline"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    headline: Mapped[str] = mapped_column(String(512), nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    ticker: Mapped["Ticker"] = relationship(back_populates="news_sentiments")


class Prediction(Base):
    """A stored ML model prediction for a ticker on a given date."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(32), nullable=False)
    predicted_direction: Mapped[int] = mapped_column(Integer, nullable=False)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    actual_direction: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    ticker: Mapped["Ticker"] = relationship(back_populates="predictions")


class Portfolio(Base):
    """A named collection of holdings."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    holdings: Mapped[list["Holding"]] = relationship(back_populates="portfolio", cascade="all, delete-orphan")


class Holding(Base):
    """A position of shares of a ticker within a portfolio."""

    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id"), nullable=False, index=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), nullable=False, index=True)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Float, nullable=False)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")
    ticker: Mapped["Ticker"] = relationship(back_populates="holdings")


_engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables if they do not already exist."""
    Base.metadata.create_all(_engine)
    logger.info("Database initialized at %s", DATABASE_URL)


@contextmanager
def get_session() -> Iterator[Session]:
    """Yield a SQLAlchemy session, committing on success and rolling back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
