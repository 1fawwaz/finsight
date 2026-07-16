"""Tests for core/ml/dataset_intelligence.py -- freshness classification and dataset
lineage lookups. Freshness-clock tests fix `now` explicitly rather than depending on the
real wall clock, so they're deterministic regardless of when the suite runs."""

from __future__ import annotations

from datetime import date, datetime

from core.database import MLDatasetVersion, get_session
from core.market_status import IST
from core.ml.dataset_intelligence import (
    assess_freshness,
    dataset_version_info,
    training_validation_periods,
)


class TestAssessFreshness:
    def test_none_date_is_unknown_not_fabricated_fresh(self):
        label, gap = assess_freshness(None)
        assert label == "Unknown"
        assert gap is None

    def test_latest_completed_session_is_fresh(self):
        # Tuesday 2026-07-14 at 16:00 IST (well after market close) -- the most recent
        # completed session is that same Tuesday.
        now = datetime(2026, 7, 14, 16, 0, tzinfo=IST)
        label, gap = assess_freshness(date(2026, 7, 14), now=now)
        assert label == "Fresh"
        assert gap == 0

    def test_one_trading_day_behind_is_delayed(self):
        now = datetime(2026, 7, 14, 16, 0, tzinfo=IST)  # Tuesday, post-close
        label, gap = assess_freshness(date(2026, 7, 13), now=now)  # Monday's bar
        assert label == "Delayed"
        assert gap == 1

    def test_several_trading_days_behind_is_stale(self):
        now = datetime(2026, 7, 16, 16, 0, tzinfo=IST)  # Thursday, post-close
        label, gap = assess_freshness(date(2026, 7, 10), now=now)  # a week-old bar
        assert label == "Stale"
        assert gap >= 2

    def test_before_market_close_expects_previous_days_bar_as_fresh(self):
        # Tuesday 2026-07-14 at 10:00 IST (market open, not yet closed) -- today's own
        # bar can't exist yet, so Monday's close is the freshest possible data.
        now = datetime(2026, 7, 14, 10, 0, tzinfo=IST)
        label, gap = assess_freshness(date(2026, 7, 13), now=now)
        assert label == "Fresh"
        assert gap == 0

    def test_weekend_now_expects_fridays_bar_as_fresh(self):
        # Saturday -- most recent completed session is the prior Friday.
        now = datetime(2026, 7, 18, 12, 0, tzinfo=IST)  # a Saturday
        label, gap = assess_freshness(date(2026, 7, 17), now=now)  # Friday's bar
        assert label == "Fresh"
        assert gap == 0


class TestDatasetVersionInfo:
    def test_none_version_returns_none(self):
        assert dataset_version_info(None) is None

    def test_unregistered_version_returns_none_not_fabricated(self, temp_db):
        assert dataset_version_info("never_registered_dataset") is None

    def test_registered_version_returns_real_lineage(self, temp_db):
        with get_session() as session:
            session.add(
                MLDatasetVersion(
                    version="test_dataset_v1",
                    start_date=date(2024, 1, 1),
                    end_date=date(2026, 1, 1),
                    row_count=1234,
                    symbol_count=5,
                    symbols_json="[]",
                    source="test",
                    quality_report_json="{}",
                )
            )
            session.flush()
        info = dataset_version_info("test_dataset_v1")
        assert info is not None
        assert info["row_count"] == 1234
        assert info["symbol_count"] == 5
        assert info["start_date"] == date(2024, 1, 1)
        assert info["end_date"] == date(2026, 1, 1)


class TestTrainingValidationPeriods:
    def test_none_feature_version_returns_none(self):
        assert training_validation_periods(None) is None

    def test_unloadable_feature_version_returns_none_not_a_crash(self, monkeypatch):
        import core.ml.dataset_intelligence as di_module

        di_module._cached_train_val_periods.cache_clear()
        monkeypatch.setattr(
            "core.ml.feature_pipeline.load_feature_set",
            lambda feature_version: (_ for _ in ()).throw(FileNotFoundError("no such feature set")),
        )
        assert training_validation_periods("never_built_features") is None
        di_module._cached_train_val_periods.cache_clear()
