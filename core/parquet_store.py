"""Phase 1 Step 16: Parquet storage -- a read-optimized columnar store for already-
validated historical market data, per docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md §7.14
and the 2026-07-13 governance amendment (docs/GOVERNANCE.md) that authorized it.

Architecture Change Rule (spec §"Architecture Change Rule") justification, stated here
rather than assumed: SQLite's `prices` table (row-oriented) requires deserializing whole
rows to compute a single column's rolling statistics across thousands of trading days --
the exact bottleneck the spec's Storage Architecture section names ("faster training
reads, lower memory footprint, better scalability"). No existing dependency in
`requirements.txt` provides columnar file I/O; `pyarrow` was already present as a
transitive dependency (pandas' Parquet engine) at `25.0.0`, confirmed via
`python -c "import pyarrow; print(pyarrow.__version__)"`, and is now pinned explicitly
rather than relied on implicitly -- a transitive dependency can disappear silently if an
upstream package drops it.

Scope decision, stated not hidden: SQLite `prices` remains the live, actively-written
system of record -- every existing app page, portfolio calculation, and ML training path
keeps reading it unchanged. Parquet here is a *derived*, explicitly-synced read-optimized
export, not a live dual-write on every ingest. A full cutover (retiring `prices` in favor
of `market_data` as spec §7.14's "never duplicate unnecessarily" implies as an eventual
end-state) is a materially larger, riskier change than this step's "smallest safe change"
scope -- logged as future work, not silently attempted.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from core.config import BASE_DIR, get_logger
from core.database import Price

logger = get_logger(__name__)

MARKET_DATA_DIR = BASE_DIR / "data" / "market_data"
MARKET_DATA_DIR.mkdir(parents=True, exist_ok=True)

_COLUMNS = ["date", "open", "high", "low", "close", "volume", "dividend", "split_ratio"]


def _partition_path(internal_id: str, year: int) -> Path:
    return MARKET_DATA_DIR / internal_id / str(year) / "data.parquet"


def sync_from_sqlite(session, internal_id: str) -> int:
    """Rewrite every year-partition Parquet file for `internal_id` from the current
    SQLite `prices` rows -- SQLite is the source of truth (per the governance
    amendment); this is a one-way, idempotent export, never the other direction.
    Returns the number of rows written across all partitions.
    """
    rows = session.execute(select(Price).where(Price.internal_id == internal_id).order_by(Price.date)).scalars().all()
    if not rows:
        logger.info("Parquet sync: no rows for internal_id=%s -- nothing to write.", internal_id)
        return 0

    df = pd.DataFrame(
        [
            {
                "date": r.date, "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "volume": r.volume, "dividend": r.dividend, "split_ratio": r.split_ratio,
            }
            for r in rows
        ]
    )
    df["year"] = df["date"].map(lambda d: d.year)

    total_written = 0
    for year, year_df in df.groupby("year"):
        path = _partition_path(internal_id, int(year))
        path.parent.mkdir(parents=True, exist_ok=True)
        year_df[_COLUMNS].to_parquet(path, engine="pyarrow", index=False)
        total_written += len(year_df)

    logger.info("Parquet sync: internal_id=%s -- %d rows across %d year-partition(s)", internal_id, total_written, df["year"].nunique())
    return total_written


def read_market_data(internal_id: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """Read back `internal_id`'s Parquet-stored history, date-indexed, across every
    year-partition that exists (or just the years overlapping [start, end] when given --
    avoids reading partitions that can't possibly contain the requested range)."""
    symbol_dir = MARKET_DATA_DIR / internal_id
    if not symbol_dir.exists():
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "dividend", "split_ratio"]).set_index(pd.DatetimeIndex([]))

    year_dirs = sorted(p for p in symbol_dir.iterdir() if p.is_dir())
    frames = []
    for year_dir in year_dirs:
        year = int(year_dir.name)
        if start is not None and year < start.year:
            continue
        if end is not None and year > end.year:
            continue
        path = year_dir / "data.parquet"
        if path.exists():
            frames.append(pd.read_parquet(path, engine="pyarrow"))

    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "dividend", "split_ratio"]).set_index(pd.DatetimeIndex([]))

    combined = pd.concat(frames, ignore_index=True).sort_values("date")
    combined = combined.set_index(pd.DatetimeIndex(combined.pop("date")))
    if start is not None:
        combined = combined[combined.index.date >= start]
    if end is not None:
        combined = combined[combined.index.date <= end]
    return combined


def sync_universe_to_parquet(session, internal_ids: list[str]) -> dict[str, int]:
    """Sync every `internal_id` in `internal_ids` -- the batch entry point for a Phase 1
    loop iteration (one unit of work per symbol, per spec §4's loop discipline)."""
    return {internal_id: sync_from_sqlite(session, internal_id) for internal_id in internal_ids}
