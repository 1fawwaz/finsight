"""Explainable-AI platform phase, Phase 7: dataset/feature lineage and data-freshness
assessment. Reuses `core.market_status`'s NSE trading-calendar logic (never
reimplements holiday/session rules) and the already-persisted `MLDatasetVersion` rows
(Phase 1 Step 12) -- this module's only new logic is (a) turning "how many trading days
behind is the latest bar" into the Fresh/Delayed/Stale/Unknown label the Engineering
Constitution's Q9 requires, and (b) a lazily-cached lookup of a feature version's real
train/validation date ranges, reusing the exact same chronological split
(`core.ml.cv.chronological_train_val_test_split`) already used at training/calibration
time (see `core.ml.registry.fit_and_store_calibration`) -- never a second splitting
implementation.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

from sqlalchemy import select

from core.config import get_logger
from core.database import MLDatasetVersion, get_session
from core.market_status import IST, MARKET_CLOSE, is_trading_day, previous_trading_day

logger = get_logger(__name__)

FRESHNESS_LEVELS = ("Fresh", "Delayed", "Stale", "Unknown")


def _most_recent_completed_session(now: datetime) -> date:
    """The most recent NSE trading day whose end-of-day bar should already exist, given
    this app's daily/EOD-only ingestion (no intraday bars are stored in `prices`)."""
    now_ist = now.astimezone(IST)
    today = now_ist.date()
    if is_trading_day(today) and now_ist.time() >= MARKET_CLOSE:
        return today
    candidate = today - timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def _trading_days_behind(latest: date, expected: date) -> int:
    if latest >= expected:
        return 0
    gap = 0
    cursor = expected
    while cursor > latest and gap < 60:  # capped -- a freshness gap this large is already unambiguously "Stale"
        cursor = previous_trading_day(cursor)
        gap += 1
    return gap


def assess_freshness(latest_market_date: date | None, now: datetime | None = None) -> tuple[str, int | None]:
    """Returns (freshness_label, trading_days_behind). `latest_market_date=None` (no
    price data at all) correctly returns `"Unknown"`, never a fabricated `"Fresh"`."""
    if latest_market_date is None:
        return "Unknown", None
    now = now or datetime.now(IST)
    expected = _most_recent_completed_session(now)
    gap = _trading_days_behind(latest_market_date, expected)
    if gap == 0:
        return "Fresh", gap
    if gap == 1:
        return "Delayed", gap
    return "Stale", gap


def dataset_version_info(dataset_version: str | None) -> dict | None:
    """Real lineage for a dataset version -- size, symbol count, date range, source --
    from the already-persisted `MLDatasetVersion` row (Phase 1 Step 12). Returns `None`
    (never a fabricated placeholder) if no version string is available (e.g. the
    in-app-fallback prediction path, which has no registered dataset) or it was never
    registered."""
    if dataset_version is None:
        return None
    with get_session() as session:
        entry = session.execute(select(MLDatasetVersion).where(MLDatasetVersion.version == dataset_version)).scalar_one_or_none()
        if entry is None:
            return None
        return {
            "version": entry.version,
            "start_date": entry.start_date,
            "end_date": entry.end_date,
            "row_count": entry.row_count,
            "symbol_count": entry.symbol_count,
            "source": entry.source,
            "created_at": entry.created_at,
        }


@lru_cache(maxsize=16)
def _cached_train_val_periods(feature_version: str) -> tuple[tuple[date, date], tuple[date, date]] | None:
    """Real train/validation date ranges for a feature version, via the same
    chronological split already used at training/calibration time. Loads the full
    historical feature set from disk -- too expensive to run on every prediction, so
    memoized per process (a feature version's split boundaries never change once its
    underlying dataset is fixed)."""
    from core.ml.cv import chronological_train_val_test_split
    from core.ml.feature_pipeline import load_feature_set

    try:
        features, _ = load_feature_set(feature_version)
        split = chronological_train_val_test_split(features)
    except Exception as exc:
        logger.warning("Could not compute train/val periods for feature_version=%s: %s", feature_version, exc)
        return None
    return (split.train_dates[0], split.train_dates[1]), (split.val_dates[0], split.val_dates[1])


def training_validation_periods(feature_version: str | None) -> tuple[tuple[date, date], tuple[date, date]] | None:
    """(train_period, val_period) as (start, end) date tuples for `feature_version`, or
    `None` if unavailable (no feature version / split couldn't be computed) -- never
    fabricated. Cached (see `_cached_train_val_periods`) since this is a property of the
    feature version, not of any individual prediction."""
    if feature_version is None:
        return None
    return _cached_train_val_periods(feature_version)
