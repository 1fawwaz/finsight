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
    # Phase 1 (additive): the permanent, ticker-change-safe identity this row belongs to,
    # per docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md §7.3 ("merge safely under
    # (internal_id, trading_date), not (symbol, trading_date)"). Nullable + no DB-level
    # UNIQUE(internal_id, date) constraint here deliberately: SQLite can't ALTER TABLE
    # ADD CONSTRAINT on a table that already holds live rows without a full table
    # rebuild (the same limitation hit for news_sentiment's UNIQUE constraint in Phase
    # 3 -- see finsight/SESSION_STATE.md), and forcing that rebuild on this table isn't
    # a "smallest safe change" for this step. Dedup-by-internal_id is enforced at the
    # application level in core.data_ingestion.upsert_prices instead; a true DB-level
    # constraint is deferred to the Parquet market_data table (Step 16), which is
    # designed with internal_id as a first-class key from the start.
    internal_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False)
    # Phase 1 Step 8 (additive): captured from yfinance's own "Dividends"/"Stock Splits"
    # columns (present by default -- yfinance's history() defaults to actions=True,
    # confirmed via PriceHistory.history's signature -- but previously discarded here).
    # NULL/absent means "no corporate action recorded on this date", not "unknown".
    dividend: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    split_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

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


# --- Phase 1 Enterprise Data Platform: symbol registry, checkpoint, operational logs ---
#
# Additive only, same as the Phase 3 block above. See docs/SCHEMA.md "Phase 1 Target
# Schema" for the full design rationale (checkpoint_state single-row decision, closed
# enums for check_name/failure_type, internal_id/year Parquet partitioning, etc.) --
# not duplicated here, since that document is the schema source of truth per the
# operating spec's own precedence rules (docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md §0).


class SymbolRegistry(Base):
    """Permanent, ticker-change-safe identity for a security. Supersedes `Ticker` as the
    join key for all new ingestion/validation/feature code; `Ticker` remains in place
    for the existing app pages until they're migrated onto `internal_id` (a separate,
    later piece of work -- not assumed done by this table's mere existence)."""

    __tablename__ = "symbol_registry"

    internal_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    current_symbol: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    historical_symbols_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    listing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    delisting_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    rename_history_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    merger_history_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"SymbolRegistry(internal_id={self.internal_id!r}, current_symbol={self.current_symbol!r})"


class CheckpointState(Base):
    """Single-row (id=1, upserted) resumption state for the Phase 1 autonomous loop.

    Deliberately single-row, not one-row-per-run: the operating spec describes one
    continuously-resumed process ("current stage, current dataset version, last
    processed date/symbol"), not concurrent independent runs needing their own history.
    Per-run audit trail belongs in the append-only logs below, not here -- see
    docs/SCHEMA.md "checkpoint_state" for the full rationale.
    """

    __tablename__ = "checkpoint_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_feature_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    last_processed_internal_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_processed_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    completed_internal_ids_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    failed_internal_ids_json: Mapped[str] = mapped_column(String, nullable=False, default="[]")
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ValidationLog(Base):
    """One row per validation check run against one symbol -- append-only. `check_name`
    is a closed enum (see docs/SCHEMA.md), not an open-ended string, so this table's
    values are always a fully enumerated, documented set."""

    __tablename__ = "validation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    internal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    passed: Mapped[bool] = mapped_column(nullable=False)
    detail_json: Mapped[str] = mapped_column(String, nullable=False, default="{}")


class ProviderHealth(Base):
    """One row per external-provider call -- append-only. `failure_type` is a closed
    enum (see docs/SCHEMA.md), null when `success` is True."""

    __tablename__ = "provider_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    internal_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    call_timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    success: Mapped[bool] = mapped_column(nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    failure_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class BackupLog(Base):
    """One row per backup taken -- append-only. `backup_path` is a bare filename,
    resolved against `core.backup.BACKUP_DIR` at restore time, the same portable-path
    convention as `MLModelRegistry.artifact_path` (an absolute path there broke across
    the host/Docker boundary -- a real bug, fixed; not repeating it here)."""

    __tablename__ = "backup_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    backup_timestamp: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    backup_path: Mapped[str] = mapped_column(String(256), nullable=False)
    verified: Mapped[bool] = mapped_column(nullable=False)
    restored_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MetadataRegistry(Base):
    """Per-`internal_id` rollup metadata (spec §7.11): core sync/versioning facts plus
    identity/context fields. Denormalized for query convenience -- `SymbolRegistry`
    remains the source of truth for identity itself."""

    __tablename__ = "metadata_registry"

    internal_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    first_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    latest_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    validation_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    last_sync: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    feature_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    dataset_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    data_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


_engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


# Columns added to an *existing* table after its initial release. `Base.metadata
# .create_all()` only issues `CREATE TABLE IF NOT EXISTS` -- it does NOT diff columns on
# a table that already exists, so a new nullable column on an existing model (like
# Price.internal_id, added in Phase 1) silently never reaches an already-created
# database without an explicit ALTER TABLE. This list is exactly that explicit,
# additive-only migration step, applied idempotently on every init_db() call. Found via
# a real regression: the ORM model referenced `prices.internal_id`, but on-disk `prices`
# (created before this column existed) didn't have it, raising
# `sqlite3.OperationalError: no such column` the moment any query touched it.
_ADDITIVE_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    # (table_name, column_name, column_ddl)
    ("prices", "internal_id", "VARCHAR(32)"),
    ("prices", "dividend", "FLOAT"),
    ("prices", "split_ratio", "FLOAT"),
]


def _apply_additive_column_migrations() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(_engine)
    existing_tables = set(inspector.get_table_names())
    with _engine.begin() as conn:
        for table_name, column_name, column_ddl in _ADDITIVE_COLUMN_MIGRATIONS:
            if table_name not in existing_tables:
                continue  # a brand-new install creates this table (with the column) via create_all() instead
            existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
            if column_name in existing_columns:
                continue
            conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}"))
            logger.info("Migration: added column %s.%s (%s)", table_name, column_name, column_ddl)


def init_db() -> None:
    """Create all tables if they do not already exist, then apply any pending additive
    column migrations to tables that already existed (see `_ADDITIVE_COLUMN_MIGRATIONS`).
    Both steps are idempotent -- safe to call on every startup."""
    Base.metadata.create_all(_engine)
    _apply_additive_column_migrations()
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
