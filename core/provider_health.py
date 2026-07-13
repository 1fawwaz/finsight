"""Phase 1 Step 14: Provider Health monitoring -- spec §7.5.

Every external-provider call is monitored continuously, not just used. This module is
the recording + query layer; `core.data_ingestion.fetch_price_history` is extended
(not duplicated) to call `record_call` around its one real external call site.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.config import get_logger
from core.database import ProviderHealth

logger = get_logger(__name__)

# Closed enum, matching docs/SCHEMA.md's provider_health.failure_type -- deliberately
# not open-ended ("etc.") so every failure is classified into one documented bucket.
FAILURE_TYPES = ("timeout", "rate_limit", "malformed_response", "auth", "connection_error", "not_found")


def record_call(
    session,
    provider: str,
    success: bool,
    internal_id: str | None = None,
    latency_ms: int | None = None,
    failure_type: str | None = None,
) -> ProviderHealth:
    if not success and failure_type is not None and failure_type not in FAILURE_TYPES:
        raise ValueError(f"failure_type {failure_type!r} is not in the closed enum {FAILURE_TYPES}")
    row = ProviderHealth(
        provider=provider,
        internal_id=internal_id,
        success=success,
        latency_ms=latency_ms,
        failure_type=failure_type if not success else None,
    )
    session.add(row)
    session.flush()
    return row


@contextmanager
def track_call(session, provider: str, internal_id: str | None = None, failure_type_on_error: str = "connection_error"):
    """Context manager wrapping one external call: records success + latency on a clean
    exit, or failure + latency on any exception (re-raised afterward -- this module
    observes calls, it never swallows their errors)."""
    start = time.monotonic()
    try:
        yield
    except Exception:
        latency_ms = int((time.monotonic() - start) * 1000)
        record_call(session, provider, success=False, internal_id=internal_id, latency_ms=latency_ms, failure_type=failure_type_on_error)
        raise
    else:
        latency_ms = int((time.monotonic() - start) * 1000)
        record_call(session, provider, success=True, internal_id=internal_id, latency_ms=latency_ms)


@dataclass
class ProviderHealthSummary:
    provider: str
    window_calls: int
    success_count: int
    success_rate: float
    latency_p50_ms: float | None
    latency_p95_ms: float | None
    failure_breakdown: dict[str, int]
    last_successful_sync: datetime | None


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return sorted_values[idx]


def summarize_provider_health(session, provider: str, window_hours: int = 24) -> ProviderHealthSummary:
    """Rolling-window health summary (spec §7.5: success rate, p50/p95 latency, failure
    breakdown, last successful sync) for `provider` over the last `window_hours`."""
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=window_hours)
    rows = session.execute(
        select(ProviderHealth).where(ProviderHealth.provider == provider, ProviderHealth.call_timestamp >= cutoff)
    ).scalars().all()

    if not rows:
        return ProviderHealthSummary(provider, 0, 0, 0.0, None, None, {}, None)

    successes = [r for r in rows if r.success]
    latencies = sorted(r.latency_ms for r in rows if r.latency_ms is not None)
    failure_breakdown: dict[str, int] = {}
    for r in rows:
        if not r.success and r.failure_type:
            failure_breakdown[r.failure_type] = failure_breakdown.get(r.failure_type, 0) + 1

    last_success = max((r.call_timestamp for r in successes), default=None)

    return ProviderHealthSummary(
        provider=provider,
        window_calls=len(rows),
        success_count=len(successes),
        success_rate=round(100.0 * len(successes) / len(rows), 2),
        latency_p50_ms=_percentile(latencies, 0.50) if latencies else None,
        latency_p95_ms=_percentile(latencies, 0.95) if latencies else None,
        failure_breakdown=failure_breakdown,
        last_successful_sync=last_success,
    )
