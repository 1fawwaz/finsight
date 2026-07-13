"""Phase 1 Step 4: Historical backfill -- full-history ingestion, resolved through the
Symbol Registry and checkpointed for safe resumption.

Reuses `core.data_ingestion.ingest_ticker` (the existing idempotent fetch/validate/upsert
pipeline) rather than duplicating it -- this module adds only what Phase 1 requires on
top: `internal_id` resolution and checkpoint-based resumability. Corporate-action
capture (adjusted close, dividends, splits -- spec §7.2's other fields) is deliberately
out of scope here; it's Step 8's job, so this table's columns aren't touched twice.

`HISTORICAL_BACKFILL_PERIOD = "max"` satisfies spec §7.2's "from 2010-01-01 (or earliest
available)" without hardcoding a start date that could predate a stock's actual listing
-- yfinance's "max" period already returns "the earliest available" per symbol.
"""

from __future__ import annotations

from core.checkpoint import is_completed, mark_completed, mark_failed, start_stage
from core.config import get_logger
from core.data_ingestion import IngestionError, ingest_ticker
from core.database import get_session
from core.symbol_registry import get_or_create

logger = get_logger(__name__)

HISTORICAL_BACKFILL_PERIOD = "max"


def backfill_symbol(symbol: str) -> tuple[str, int]:
    """Full-history ingest for one symbol. Returns (internal_id, rows_inserted).
    Raises IngestionError on failure -- the caller (backfill_universe, or a manual
    caller) classifies and checkpoints per spec §11."""
    with get_session() as session:
        registry_entry = get_or_create(session, symbol)
        internal_id = registry_entry.internal_id

    inserted = ingest_ticker(symbol, period=HISTORICAL_BACKFILL_PERIOD)
    logger.info("Historical backfill: %s (internal_id=%s) -- %d rows inserted", symbol, internal_id, inserted)
    return internal_id, inserted


def backfill_universe(symbols: list[str]) -> dict[str, int]:
    """Backfill every symbol in `symbols`, checkpointed so an interrupted run resumes
    without redoing already-completed symbols (spec §7.16). Non-recoverable per-symbol
    failures are logged and checkpointed as failed, not retried inline here -- retry
    itself is `core.ml.data_layer._ingest_with_retry`'s job (already built, spec §7.4),
    reused by ingest_ticker's callers where retry semantics are wanted."""
    with get_session() as session:
        start_stage(session, "historical_backfill")

    results: dict[str, int] = {}
    for symbol in symbols:
        with get_session() as session:
            registry_entry = get_or_create(session, symbol)
            internal_id = registry_entry.internal_id
            if is_completed(session, internal_id):
                logger.info("Historical backfill: %s (internal_id=%s) already completed this stage, skipping.", symbol, internal_id)
                continue

        try:
            _, inserted = backfill_symbol(symbol)
            results[symbol] = inserted
            with get_session() as session:
                mark_completed(session, internal_id)
        except IngestionError as exc:
            results[symbol] = 0
            with get_session() as session:
                mark_failed(session, internal_id, str(exc))
            logger.error("Historical backfill: %s (internal_id=%s) failed -- %s", symbol, internal_id, exc)

    return results
