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


class Watchlist(Base):
    """A ticker the user is tracking on the Market Overview page.

    Persisted in the DB (rather than session state) so it survives restarts and is
    shared consistently across the whole app, the same way portfolio holdings are.
    """

    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("ticker_id", name="uq_watchlist_ticker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), nullable=False, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    ticker: Mapped["Ticker"] = relationship()


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


# --- Phase 3 ML pipeline: dataset versioning, feature store, model registry ----------
#
# All additive (no existing table is altered), so this is safe under init_db()'s
# CREATE-TABLE-IF-NOT-EXISTS semantics -- no migration, no risk to existing rows. A
# pre-change backup of finsight.db was still taken to data/backups/ before these were
# added, per the project's database-safety standard.


class MLDatasetVersion(Base):
    """A named, reproducible snapshot of the prices table used to train a model:
    exactly which symbols, which date range, how many rows, and what quality checks
    were run -- so a model trained months ago can be traced back to precisely the data
    it saw."""

    __tablename__ = "ml_dataset_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False)
    symbols_json: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    quality_report_json: Mapped[str] = mapped_column(String, nullable=False)


class MLFeatureSet(Base):
    """One versioned run of the feature-engineering pipeline against a dataset version:
    which features, generated by which code, when."""

    __tablename__ = "ml_feature_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    feature_names_json: Mapped[str] = mapped_column(String, nullable=False)
    pipeline_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)

    values: Mapped[list["MLFeatureValue"]] = relationship(back_populates="feature_set", cascade="all, delete-orphan")


class MLFeatureValue(Base):
    """One row of engineered features for one (ticker, date) under a given feature set --
    the feature store itself. Recomputing historical values is unnecessary as long as the
    feature set's own definition hasn't changed; new candles only add new rows here."""

    __tablename__ = "ml_feature_values"
    __table_args__ = (UniqueConstraint("feature_set_id", "ticker_id", "date", name="uq_feature_value_set_ticker_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_set_id: Mapped[int] = mapped_column(ForeignKey("ml_feature_sets.id"), nullable=False, index=True)
    ticker_id: Mapped[int] = mapped_column(ForeignKey("tickers.id"), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    features_json: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    feature_set: Mapped["MLFeatureSet"] = relationship(back_populates="values")
    ticker: Mapped["Ticker"] = relationship()


class MLTrainingRun(Base):
    """One trial (one hyperparameter configuration, one model family) from the Optuna
    search -- every trial is logged here, not just the eventual winner."""

    __tablename__ = "ml_training_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_family: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trial_number: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    hyperparameters_json: Mapped[str] = mapped_column(String, nullable=False)
    metrics_json: Mapped[str] = mapped_column(String, nullable=False)
    fold_metrics_json: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class MLModelRegistry(Base):
    """The persisted record for a model that passed the generalization gate and was
    selected -- artifact location, full lineage (dataset/feature version, git commit),
    hyperparameters, and metrics, so a deployed model is always traceable back to
    exactly what produced it."""

    __tablename__ = "ml_model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_name: Mapped[str] = mapped_column(String(64), nullable=False)
    model_family: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(64), nullable=False)
    hyperparameters_json: Mapped[str] = mapped_column(String, nullable=False)
    metrics_json: Mapped[str] = mapped_column(String, nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(256), nullable=False)
    git_commit_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)


class MLImprovementIteration(Base):
    """One iteration of the Step 2.9 autonomous improvement loop -- every iteration is
    logged here, including reverted ones, so the deployed model is traceable to the
    best-observed result across the full logged history, not just the last run."""

    __tablename__ = "ml_improvement_iterations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    iteration_number: Mapped[int] = mapped_column(Integer, nullable=False)
    change_description: Mapped[str] = mapped_column(String(512), nullable=False)
    hypothesis: Mapped[str] = mapped_column(String(512), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_before: Mapped[float] = mapped_column(Float, nullable=False)
    metric_after: Mapped[float] = mapped_column(Float, nullable=False)
    secondary_metrics_json: Mapped[str] = mapped_column(String, nullable=False)
    relative_improvement_pct: Mapped[float] = mapped_column(Float, nullable=False)
    test_results: Mapped[str] = mapped_column(String(128), nullable=False)
    regression_check: Mapped[str] = mapped_column(String(256), nullable=False)
    kept: Mapped[bool] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


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
