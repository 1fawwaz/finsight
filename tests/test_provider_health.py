"""Tests for core.provider_health: call recording and rolling-window summaries (spec §7.5)."""

from datetime import datetime, timedelta, timezone

import pytest

from core.database import ProviderHealth
from core.provider_health import record_call, summarize_provider_health, track_call


def test_record_call_success(db_session):
    row = record_call(db_session, "yfinance", success=True, internal_id="FIN-0001", latency_ms=150)
    assert row.success is True
    assert row.failure_type is None
    assert row.latency_ms == 150


def test_record_call_failure_requires_valid_failure_type(db_session):
    with pytest.raises(ValueError, match="not in the closed enum"):
        record_call(db_session, "yfinance", success=False, failure_type="made_up_reason")


def test_record_call_failure_stores_classified_type(db_session):
    row = record_call(db_session, "yfinance", success=False, failure_type="timeout", latency_ms=8000)
    assert row.success is False
    assert row.failure_type == "timeout"


def test_track_call_records_success_and_returns_normally(db_session):
    with track_call(db_session, "yfinance", internal_id="FIN-0001"):
        pass  # the wrapped call succeeds

    row = db_session.query(ProviderHealth).one()
    assert row.success is True
    assert row.latency_ms is not None


def test_track_call_records_failure_and_reraises(db_session):
    with pytest.raises(RuntimeError, match="boom"):
        with track_call(db_session, "yfinance", failure_type_on_error="connection_error"):
            raise RuntimeError("boom")

    row = db_session.query(ProviderHealth).one()
    assert row.success is False
    assert row.failure_type == "connection_error"


def test_summarize_provider_health_computes_success_rate_and_latency(db_session):
    record_call(db_session, "yfinance", success=True, latency_ms=100)
    record_call(db_session, "yfinance", success=True, latency_ms=200)
    record_call(db_session, "yfinance", success=False, latency_ms=5000, failure_type="timeout")

    summary = summarize_provider_health(db_session, "yfinance")

    assert summary.window_calls == 3
    assert summary.success_count == 2
    assert round(summary.success_rate, 2) == 66.67
    assert summary.failure_breakdown == {"timeout": 1}
    assert summary.latency_p50_ms is not None


def test_summarize_provider_health_excludes_calls_outside_window(db_session):
    old_row = ProviderHealth(provider="yfinance", success=True, latency_ms=100)
    db_session.add(old_row)
    db_session.flush()
    old_row.call_timestamp = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=48)
    db_session.flush()

    record_call(db_session, "yfinance", success=True, latency_ms=100)

    summary = summarize_provider_health(db_session, "yfinance", window_hours=24)

    assert summary.window_calls == 1  # the 48h-old row is outside the 24h window


def test_summarize_provider_health_no_calls_returns_empty_summary(db_session):
    summary = summarize_provider_health(db_session, "yfinance")

    assert summary.window_calls == 0
    assert summary.success_rate == 0.0
    assert summary.last_successful_sync is None


def test_summarize_provider_health_last_successful_sync_ignores_failures(db_session):
    record_call(db_session, "yfinance", success=False, failure_type="timeout")
    record_call(db_session, "yfinance", success=True)

    summary = summarize_provider_health(db_session, "yfinance")

    assert summary.last_successful_sync is not None
