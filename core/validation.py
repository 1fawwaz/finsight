"""Phase 1 Step 10: Validation framework -- spec §7.8's full checklist, persisted to
`validation_log` under the closed `check_name` enum documented in docs/SCHEMA.md.

Reuses `core.ml.data_layer.validate_symbol_history` (schema/duplicate/range/outlier
detection, already built and tested in Phase 3) and
`core.corporate_actions.validate_corporate_action_consistency` (Step 8) rather than
reimplementing detection logic -- this module maps their existing results onto the
closed check-name set and adds the two checks nothing existing covered: NSE-calendar
reconciliation (reusing `core.market_status`, not a new calendar source) and
Symbol Registry identity consistency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as date_type

import pandas as pd
from sqlalchemy import select

from core.config import get_logger
from core.corporate_actions import validate_corporate_action_consistency
from core.database import Price, SymbolRegistry, ValidationLog
from core.market_status import is_trading_day
from core.ml.data_layer import validate_symbol_history

logger = get_logger(__name__)

CHECK_NAMES = (
    "ohlc_integrity",
    "duplicate_row",
    "missing_date_calendar",
    "calendar_consistency",
    "symbol_identity",
    "volume_anomaly",
    "price_anomaly",
    "adjusted_close_consistency",
    "corporate_action_consistency",
    "timestamp_ordering",
)


@dataclass
class CheckResult:
    check_name: str
    passed: bool
    detail: dict = field(default_factory=dict)


@dataclass
class FullValidationReport:
    internal_id: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def result_for(self, check_name: str) -> CheckResult | None:
        return next((r for r in self.results if r.check_name == check_name), None)


def _load_price_rows(session, internal_id: str) -> list[Price]:
    return session.execute(select(Price).where(Price.internal_id == internal_id).order_by(Price.date)).scalars().all()


def _to_dataframe(rows: list[Price]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(
        [{"date": r.date, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for r in rows]
    )
    return df.set_index(pd.DatetimeIndex(df.pop("date")))


def _check_calendar(rows: list[Price]) -> tuple[CheckResult, CheckResult]:
    """Two checks from one pass over the date range: missing_date_calendar (a real
    trading day with no candle) and calendar_consistency (a candle on a date that isn't
    a trading day at all -- weekend/holiday data that shouldn't exist)."""
    if not rows:
        empty_detail = {"reason": "no price rows for this internal_id"}
        return (
            CheckResult("missing_date_calendar", True, empty_detail),
            CheckResult("calendar_consistency", True, empty_detail),
        )

    present_dates = {r.date for r in rows}
    start, end = min(present_dates), max(present_dates)

    missing: list[str] = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor) and cursor not in present_dates:
            missing.append(cursor.isoformat())
        cursor = date_type.fromordinal(cursor.toordinal() + 1)

    non_trading_day_rows = [d.isoformat() for d in present_dates if not is_trading_day(d)]

    return (
        CheckResult("missing_date_calendar", len(missing) == 0, {"missing_trading_dates": missing[:50], "missing_count": len(missing)}),
        CheckResult("calendar_consistency", len(non_trading_day_rows) == 0, {"non_trading_day_rows": non_trading_day_rows[:50]}),
    )


def _check_symbol_identity(session, internal_id: str, rows: list[Price]) -> CheckResult:
    registry_entry = session.execute(select(SymbolRegistry).where(SymbolRegistry.internal_id == internal_id)).scalar_one_or_none()
    if registry_entry is None:
        return CheckResult("symbol_identity", False, {"reason": f"no SymbolRegistry entry for internal_id {internal_id!r}"})
    orphaned = [r.date.isoformat() for r in rows if r.internal_id is None]
    return CheckResult("symbol_identity", len(orphaned) == 0, {"registry_symbol": registry_entry.current_symbol, "orphaned_rows": orphaned[:50]})


def _check_volume_anomaly(rows: list[Price]) -> CheckResult:
    negative = [r.date.isoformat() for r in rows if r.volume < 0]
    return CheckResult("volume_anomaly", len(negative) == 0, {"negative_volume_dates": negative})


def _check_adjusted_close_consistency() -> CheckResult:
    """Vacuous pass, explicitly labeled: adjusted-close is not yet captured (a stated
    scope decision from Step 8 -- see core.corporate_actions module docstring), so there
    is nothing to check yet. Logged honestly as not-applicable rather than fabricating a
    real numeric result for data that doesn't exist."""
    return CheckResult(
        "adjusted_close_consistency", True,
        {"status": "not_applicable", "reason": "adjusted_close not yet captured -- deferred to Parquet market_data (Step 16)"},
    )


def run_full_validation(session, internal_id: str) -> FullValidationReport:
    """Run every check in spec §7.8's checklist for `internal_id` and persist each to
    `validation_log`. Bad data is flagged, never silently discarded -- every check's
    result is logged regardless of pass/fail."""
    rows = _load_price_rows(session, internal_id)
    df = _to_dataframe(rows)
    quality_report = validate_symbol_history(internal_id, df)

    missing_check, calendar_check = _check_calendar(rows)
    corporate_action_report = validate_corporate_action_consistency(session, internal_id)

    results = [
        CheckResult("ohlc_integrity", len(quality_report.range_violations) == 0, {"violations": quality_report.range_violations, "missing_value_rows": quality_report.missing_value_rows}),
        CheckResult("duplicate_row", quality_report.duplicate_dates == 0, {"duplicate_dates": quality_report.duplicate_dates}),
        missing_check,
        calendar_check,
        _check_symbol_identity(session, internal_id, rows),
        _check_volume_anomaly(rows),
        CheckResult("price_anomaly", len(quality_report.outlier_days) == 0, {"outlier_days": quality_report.outlier_days}),
        _check_adjusted_close_consistency(),
        CheckResult("corporate_action_consistency", corporate_action_report.passed, {"unexplained_large_moves": [d.isoformat() for d in corporate_action_report.unexplained_large_moves]}),
        CheckResult("timestamp_ordering", quality_report.out_of_order_timestamps == 0, {"out_of_order_timestamps": quality_report.out_of_order_timestamps}),
    ]

    for result in results:
        session.add(
            ValidationLog(
                internal_id=internal_id,
                check_name=result.check_name,
                passed=result.passed,
                detail_json=json.dumps(result.detail, default=str),
            )
        )
    session.flush()

    report = FullValidationReport(internal_id=internal_id, results=results)
    if not report.all_passed:
        failed = [r.check_name for r in results if not r.passed]
        logger.warning("Validation: internal_id=%s failed checks: %s", internal_id, failed)
    return report
