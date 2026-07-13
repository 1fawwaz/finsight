"""Phase 1 Step 11: Metadata Registry -- per-internal_id rollup metadata (spec §7.11).

Reuses `core.validation.run_full_validation`'s persisted results (Step 10) for
`validation_status` rather than recomputing pass/fail here, and
`core.symbol_registry` for identity/exchange context -- this module is a thin rollup
over data other Phase 1 modules already produce, not a new source of truth.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select

from core.config import get_logger
from core.database import MetadataRegistry, Price, SymbolRegistry, ValidationLog

logger = get_logger(__name__)

CURRENCY = "INR"  # India-only scope, per docs/GOVERNANCE.md -- no multi-currency logic anywhere
TIMEZONE = "Asia/Kolkata"
DATA_PROVIDER = "yfinance"


def _exchange_for_symbol(symbol: str) -> str | None:
    if symbol.endswith(".NS"):
        return "NSE"
    if symbol.endswith(".BO"):
        return "BSE"
    return None  # benchmark indices (^NSEI etc.) have no single exchange in this sense


def _compute_checksum(rows: list[Price]) -> str | None:
    """Reproducible hash of the price series -- same purpose as
    core.ml.feature_pipeline._pipeline_code_hash (ties a stored fact to exactly the data
    that produced it), applied here to price data instead of feature-generation code."""
    if not rows:
        return None
    payload = "|".join(f"{r.date.isoformat()},{r.close}" for r in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def refresh_metadata(
    session,
    internal_id: str,
    feature_version: str | None = None,
    dataset_version: str | None = None,
) -> MetadataRegistry:
    """Recompute and upsert the metadata rollup for `internal_id`. Idempotent -- safe to
    call repeatedly (e.g. after every ingestion run); always reflects the current state
    of `prices` and the most recent validation run, not a stale snapshot.
    """
    rows = session.execute(select(Price).where(Price.internal_id == internal_id).order_by(Price.date)).scalars().all()
    registry_entry = session.execute(select(SymbolRegistry).where(SymbolRegistry.internal_id == internal_id)).scalar_one_or_none()

    latest_validation = session.execute(
        select(ValidationLog).where(ValidationLog.internal_id == internal_id).order_by(ValidationLog.run_timestamp.desc())
    ).scalars().first()
    if latest_validation is None:
        validation_status = "not_validated"
    else:
        recent_run_checks = session.execute(
            select(ValidationLog)
            .where(ValidationLog.internal_id == internal_id, ValidationLog.run_timestamp == latest_validation.run_timestamp)
        ).scalars().all()
        validation_status = "passed" if all(c.passed for c in recent_run_checks) else "failed"

    entry = session.get(MetadataRegistry, internal_id)
    if entry is None:
        entry = MetadataRegistry(internal_id=internal_id)
        session.add(entry)

    entry.first_date = rows[0].date if rows else None
    entry.latest_date = rows[-1].date if rows else None
    entry.row_count = len(rows)
    entry.checksum = _compute_checksum(rows)
    entry.validation_status = validation_status
    entry.last_sync = datetime.now(timezone.utc)
    if feature_version is not None:
        entry.feature_version = feature_version
    if dataset_version is not None:
        entry.dataset_version = dataset_version
    entry.exchange = _exchange_for_symbol(registry_entry.current_symbol) if registry_entry else None
    entry.currency = CURRENCY
    entry.timezone = TIMEZONE
    entry.data_provider = DATA_PROVIDER

    session.flush()
    logger.info(
        "Metadata registry refreshed: internal_id=%s rows=%d validation_status=%s",
        internal_id, entry.row_count, validation_status,
    )
    return entry
