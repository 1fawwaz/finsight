"""Phase 3 Data Layer: dataset versioning and quality validation on top of the existing
incremental price-ingestion pipeline.

Acquisition and incremental sync are already handled by `core.data_ingestion.ingest_ticker`
(a real SQLite UPSERT that only inserts dates not already present -- no full re-pull ever
happens on a re-run). This module does not duplicate that; it adds what Phase 3 requires
on top: per-run quality validation, and a named, reproducible "dataset version" snapshot
of the prices table with full metadata persisted alongside it.

A dataset version is a *pointer* (symbol list + date range), not a copy of the price
rows -- `prices` is already the single source of truth and is append-only (existing rows
for a given (ticker, date) are never modified after insert), so recording the pointer is
sufficient for exact reproducibility from metadata alone.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

import pandas as pd
from sqlalchemy import select

from core.config import get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.database import MLDatasetVersion, get_session
from core.queries import get_price_history
from core.symbol_registry import get_or_create as get_or_create_symbol_registry_entry

logger = get_logger(__name__)

# Below this many trading days (~2 years), a symbol's history is too short for reliable
# time-series-CV feature computation (rolling windows up to 252 days) and is excluded
# from a dataset version rather than silently trained on with mostly-NaN features.
MIN_ROWS_FOR_TRAINING = 500


@dataclass
class SymbolQualityReport:
    """Per-symbol validation result. Every check is recorded, not just failures, so a
    passing symbol's report is still evidence something was actually checked."""

    symbol: str
    row_count: int
    schema_valid: bool
    duplicate_dates: int
    out_of_order_timestamps: int
    missing_value_rows: int
    range_violations: list[str] = field(default_factory=list)
    outlier_days: list[str] = field(default_factory=list)
    included_in_dataset: bool = True
    exclusion_reason: str | None = None
    # Phase 1 (additive, default None so this dataclass stays backward compatible for
    # any existing caller constructing one without it): the symbol's permanent identity,
    # per docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md §7.9 ("the internal_id set covered").
    internal_id: str | None = None


@dataclass
class DatasetQualityReport:
    """Aggregate quality report for a full dataset version, persisted as JSON alongside
    the version's metadata."""

    symbol_reports: list[SymbolQualityReport]
    total_rows: int
    included_symbols: list[str]
    excluded_symbols: list[str]

    @property
    def included_internal_ids(self) -> list[str]:
        return [r.internal_id for r in self.symbol_reports if r.included_in_dataset and r.internal_id is not None]

    def to_json(self) -> str:
        return json.dumps(
            {
                "total_rows": self.total_rows,
                "included_symbols": self.included_symbols,
                "excluded_symbols": self.excluded_symbols,
                "included_internal_ids": self.included_internal_ids,
                # Point-in-time index constituent history (spec §7.6) is not tracked --
                # blocked on an authoritative Nifty constituent-membership dataset that
                # does not exist in this repository (Phase 1 Steps 6/7/9, recorded as
                # blocked in finsight/PHASE1_IMPLEMENTATION_LOG.md). Stated explicitly
                # here rather than silently omitted, so a manifest reader never assumes
                # this dataset version is survivorship-bias-safe when it isn't.
                "constituent_history": "not_available -- blocked pending an authoritative Nifty index-constituent dataset (see PHASE1_IMPLEMENTATION_LOG.md)",
                "symbol_reports": [
                    {
                        "symbol": r.symbol,
                        "internal_id": r.internal_id,
                        "row_count": r.row_count,
                        "schema_valid": r.schema_valid,
                        "duplicate_dates": r.duplicate_dates,
                        "out_of_order_timestamps": r.out_of_order_timestamps,
                        "missing_value_rows": r.missing_value_rows,
                        "range_violations": r.range_violations,
                        "outlier_days": r.outlier_days,
                        "included_in_dataset": r.included_in_dataset,
                        "exclusion_reason": r.exclusion_reason,
                    }
                    for r in self.symbol_reports
                ],
            },
            default=str,
        )


def _ingest_with_retry(symbol: str, period: str, max_attempts: int = 3, backoff_seconds: float = 1.0) -> int:
    """ingest_ticker wrapped with retries for transient failures (network hiccups,
    momentary yfinance rate-limit responses) -- a fixed short backoff between attempts,
    not a tight retry loop that would hammer an already-struggling upstream."""
    last_exc: IngestionError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return ingest_ticker(symbol, period=period)
        except IngestionError as exc:
            last_exc = exc
            if attempt < max_attempts:
                logger.warning("Data layer: %s fetch failed (attempt %d/%d) -- %s. Retrying.", symbol, attempt, max_attempts, exc)
                time.sleep(backoff_seconds * attempt)
    raise last_exc  # all attempts exhausted -- surface the last real error to the caller


def sync_universe(symbols: list[str], period: str = "5y") -> dict[str, int]:
    """Incrementally sync price history for `symbols` (idempotent -- only new dates are
    inserted; a re-run does no redundant work). Retries each symbol up to 3 times on a
    transient fetch failure before giving up on it. Returns {symbol: rows_inserted},
    logging and skipping (not raising for) any symbol that still fails after retries,
    per the "skip invalid rows or symbols rather than silently training on them, and log
    what was skipped and why" data-quality rule.
    """
    results: dict[str, int] = {}
    for symbol in symbols:
        try:
            results[symbol] = _ingest_with_retry(symbol, period)
        except IngestionError as exc:
            logger.warning("Data layer: skipping %s after retries exhausted -- %s", symbol, exc)
            results[symbol] = 0
    return results


def validate_symbol_history(symbol: str, history: pd.DataFrame) -> SymbolQualityReport:
    """Schema, range, duplicate, ordering, and outlier checks for one symbol's OHLCV
    history. Never mutates `history` -- flags issues for the aggregate report rather than
    silently dropping rows here (upsert_prices already rejects NaN OHLCV rows at
    ingestion time, so what's checked here is data already accepted into the DB)."""
    required_cols = {"open", "high", "low", "close", "volume"}
    schema_valid = required_cols.issubset(set(history.columns))

    row_count = len(history)
    if row_count == 0 or not schema_valid:
        return SymbolQualityReport(
            symbol=symbol,
            row_count=row_count,
            schema_valid=schema_valid,
            duplicate_dates=0,
            out_of_order_timestamps=0,
            missing_value_rows=0,
            included_in_dataset=False,
            exclusion_reason="empty history" if row_count == 0 else "missing required OHLCV columns",
        )

    duplicate_dates = int(history.index.duplicated().sum())
    out_of_order = int((history.index.to_series().diff().dropna() < pd.Timedelta(0)).sum())
    missing_value_rows = int(history[list(required_cols)].isna().any(axis=1).sum())

    range_violations: list[str] = []
    bad_hl = history[history["high"] < history["low"]]
    if not bad_hl.empty:
        range_violations.append(f"high < low on {len(bad_hl)} day(s)")
    bad_high = history[(history["high"] < history["open"]) | (history["high"] < history["close"])]
    if not bad_high.empty:
        range_violations.append(f"high below open/close on {len(bad_high)} day(s)")
    bad_low = history[(history["low"] > history["open"]) | (history["low"] > history["close"])]
    if not bad_low.empty:
        range_violations.append(f"low above open/close on {len(bad_low)} day(s)")
    non_positive_close = history[history["close"] <= 0]
    if not non_positive_close.empty:
        range_violations.append(f"non-positive close on {len(non_positive_close)} day(s)")
    negative_volume = history[history["volume"] < 0]
    if not negative_volume.empty:
        range_violations.append(f"negative volume on {len(negative_volume)} day(s)")

    # A single-day move beyond +/-40% is far outside normal equity behavior for an
    # already-listed large/mid-cap stock and is far more likely a stock-split/bonus
    # discontinuity than real price action (upsert_prices doesn't adjust for corporate
    # actions) -- flagged for review, not silently dropped, since a real circuit-limit
    # or news-driven move can occasionally be this large too.
    daily_return = history["close"].pct_change()
    outlier_mask = daily_return.abs() > 0.40
    outlier_days = [str(d.date()) for d in history.index[outlier_mask.fillna(False)]]

    included = row_count >= MIN_ROWS_FOR_TRAINING
    exclusion_reason = None if included else f"only {row_count} rows (< {MIN_ROWS_FOR_TRAINING} minimum for reliable feature windows)"

    return SymbolQualityReport(
        symbol=symbol,
        row_count=row_count,
        schema_valid=schema_valid,
        duplicate_dates=duplicate_dates,
        out_of_order_timestamps=out_of_order,
        missing_value_rows=missing_value_rows,
        range_violations=range_violations,
        outlier_days=outlier_days,
        included_in_dataset=included,
        exclusion_reason=exclusion_reason,
    )


def _next_version_name() -> str:
    with get_session() as session:
        count = session.execute(select(MLDatasetVersion)).scalars().all()
        return f"dataset_v{len(count) + 1}"


def create_dataset_version(symbols: list[str], version_name: str | None = None) -> MLDatasetVersion:
    """Validate and version the current price data for `symbols`. Symbols failing
    validation (insufficient history, bad schema) are excluded from the version but
    still recorded in its quality report with the reason -- never silently dropped.
    """
    symbol_reports: list[SymbolQualityReport] = []
    included_symbols: list[str] = []
    frames: dict[str, pd.DataFrame] = {}

    for symbol in symbols:
        history = get_price_history(symbol)
        report = validate_symbol_history(symbol, history)
        with get_session() as session:
            report.internal_id = get_or_create_symbol_registry_entry(session, symbol).internal_id
        symbol_reports.append(report)
        if report.included_in_dataset:
            included_symbols.append(symbol)
            frames[symbol] = history
        else:
            logger.warning("Dataset version: excluding %s -- %s", symbol, report.exclusion_reason)

    if not included_symbols:
        raise ValueError("No symbols passed quality validation -- cannot create a dataset version from zero data.")

    total_rows = sum(len(df) for df in frames.values())
    start_date = min(df.index.min() for df in frames.values()).date()
    end_date = max(df.index.max() for df in frames.values()).date()

    quality_report = DatasetQualityReport(
        symbol_reports=symbol_reports,
        total_rows=total_rows,
        included_symbols=included_symbols,
        excluded_symbols=[r.symbol for r in symbol_reports if not r.included_in_dataset],
    )

    version = version_name or _next_version_name()
    with get_session() as session:
        record = MLDatasetVersion(
            version=version,
            start_date=start_date,
            end_date=end_date,
            row_count=total_rows,
            symbol_count=len(included_symbols),
            symbols_json=json.dumps(included_symbols),
            source="SQLite prices table (core.database.Price), via core.queries.get_price_history",
            quality_report_json=quality_report.to_json(),
        )
        session.add(record)
        session.flush()
        logger.info(
            "Created dataset version %s: %d symbols, %d rows, %s to %s (%d excluded)",
            version,
            len(included_symbols),
            total_rows,
            start_date,
            end_date,
            len(quality_report.excluded_symbols),
        )
        # SessionLocal is expire_on_commit=False, so `record`'s already-loaded
        # attributes stay readable after the session commits and closes below.
        return record


def get_dataset_version(version: str) -> MLDatasetVersion | None:
    with get_session() as session:
        return session.execute(select(MLDatasetVersion).where(MLDatasetVersion.version == version)).scalar_one_or_none()


def load_dataset(version: str) -> dict[str, pd.DataFrame]:
    """Reload the exact price data a dataset version pointed to, by symbol. Reproducible
    from metadata alone: the recorded symbol list, re-queried from the same append-only
    `prices` table -- rows for dates already recorded are never modified after insert."""
    record = get_dataset_version(version)
    if record is None:
        raise ValueError(f"No dataset version named {version!r} in the registry.")
    symbols = json.loads(record.symbols_json)
    return {symbol: get_price_history(symbol) for symbol in symbols}
