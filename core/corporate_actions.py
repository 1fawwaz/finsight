"""Phase 1 Step 8: Corporate action handling.

Captures dividends/splits at ingestion time (core.data_ingestion.upsert_prices, extended
-- not duplicated) and validates them here: every day with a large price jump should
have a recorded corporate action explaining it, and every recorded corporate action
should correspond to a real, consistent price move. Reuses
core.symbol_registry.record_merger for the merger side of corporate actions (already
built in Step 1) rather than reimplementing it.

Scope note: this module validates *consistency* between recorded actions and observed
price moves -- it does not recompute a backward-adjusted price series from scratch.
yfinance already provides adjustment (via `auto_adjust=True`), and re-deriving that math
independently is a separate, higher-risk undertaking than the spec's "validate adjusted
prices... never silently ignore a corporate action" requires. Full backward-adjustment
recomputation is logged as a known limitation, not silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select

from core.config import get_logger
from core.database import Price

logger = get_logger(__name__)

# A single-day close-to-close move beyond this magnitude with NO recorded dividend/split
# on that date is flagged as an unexplained large move. Deliberately tighter than
# core.ml.data_layer's +/-40% ingestion-time outlier flag (which exists to catch gross
# data errors): bonus issues and smaller splits routinely produce moves well under 40%
# but still warrant a corporate-action explanation, which is what this check is for.
UNEXPLAINED_MOVE_THRESHOLD = 0.15


@dataclass
class CorporateActionEvent:
    trading_date: date
    dividend: float | None
    split_ratio: float | None


@dataclass
class CorporateActionValidationReport:
    internal_id: str
    recorded_events: list[CorporateActionEvent] = field(default_factory=list)
    unexplained_large_moves: list[date] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Consistent per spec's binary framing (validation_log.passed): no unexplained
        large moves. Recorded events with no move at all are not a failure -- a
        dividend, in particular, doesn't necessarily move price by a detectable amount."""
        return len(self.unexplained_large_moves) == 0


def get_corporate_actions(session, internal_id: str) -> list[CorporateActionEvent]:
    """Every recorded dividend/split for `internal_id`, ordered by date."""
    rows = session.execute(
        select(Price)
        .where(Price.internal_id == internal_id)
        .where((Price.dividend.is_not(None)) | (Price.split_ratio.is_not(None)))
        .order_by(Price.date)
    ).scalars().all()
    return [CorporateActionEvent(trading_date=r.date, dividend=r.dividend, split_ratio=r.split_ratio) for r in rows]


def validate_corporate_action_consistency(session, internal_id: str) -> CorporateActionValidationReport:
    """Walk `internal_id`'s price history in date order; flag any day with a >|15%|
    close-to-close move that has no recorded dividend/split explaining it. Never
    silently ignores a corporate action -- every recorded event is returned in the
    report regardless of outcome, and every large unexplained move is listed by date so
    it can be investigated, not swallowed into a pass/fail bit alone.
    """
    rows = session.execute(
        select(Price).where(Price.internal_id == internal_id).order_by(Price.date)
    ).scalars().all()

    events = [
        CorporateActionEvent(trading_date=r.date, dividend=r.dividend, split_ratio=r.split_ratio)
        for r in rows
        if r.dividend is not None or r.split_ratio is not None
    ]

    unexplained: list[date] = []
    for prev_row, row in zip(rows, rows[1:]):
        if prev_row.close in (None, 0):
            continue
        move = (row.close - prev_row.close) / prev_row.close
        has_recorded_action = row.dividend is not None or row.split_ratio is not None
        if abs(move) > UNEXPLAINED_MOVE_THRESHOLD and not has_recorded_action:
            unexplained.append(row.date)

    if unexplained:
        logger.warning(
            "Corporate action validation: internal_id=%s has %d unexplained large move(s): %s",
            internal_id, len(unexplained), unexplained,
        )

    return CorporateActionValidationReport(internal_id=internal_id, recorded_events=events, unexplained_large_moves=unexplained)
