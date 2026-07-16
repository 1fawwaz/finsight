"""Tests for core/ml/prediction_tracking.py -- writing and resolving real predictions.
Every test uses `temp_db` (isolated in-memory DB) to avoid touching the real
finsight.db."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import select

from core.database import Prediction, Ticker, get_session
from core.ml.confidence import assess_confidence
from core.ml.prediction_service import PredictionResult
from core.ml.prediction_tracking import record_prediction, resolve_pending_outcomes


def _seed_ticker(symbol: str) -> None:
    with get_session() as session:
        session.add(Ticker(symbol=symbol, name=symbol))
        session.flush()


def _fake_result(prob_up: float = 0.6) -> PredictionResult:
    from datetime import datetime, timezone

    result = PredictionResult(symbol="TEST.NS", generated_at=datetime.now(timezone.utc))
    result.confidence = assess_confidence(prob_up, was_calibrated=True)
    result.model_source = "registry"
    result.model_name = "test_model"
    result.model_version = "test_model_v1"
    result.model_status = "active"
    result.dataset_version = "dataset_v1"
    return result


class TestRecordPrediction:
    def test_inserts_a_new_row(self, temp_db):
        _seed_ticker("TEST.NS")
        result = _fake_result()
        inserted = record_prediction("TEST.NS", date(2026, 7, 20), result)
        assert inserted is True

        with get_session() as session:
            rows = session.execute(select(Prediction)).scalars().all()
            assert len(rows) == 1
            assert rows[0].model_version == "test_model_v1"
            assert rows[0].predicted_direction == 1  # prob_up=0.6 -> UP
            assert rows[0].confidence_level == "Low"  # score = |0.6-0.5|*2*100 = 20, which is >=15 ("Low" threshold)

    def test_duplicate_ticker_date_model_is_a_noop_not_a_duplicate_row(self, temp_db):
        _seed_ticker("TEST.NS")
        result = _fake_result()
        record_prediction("TEST.NS", date(2026, 7, 20), result)
        inserted_again = record_prediction("TEST.NS", date(2026, 7, 20), result)
        assert inserted_again is False

        with get_session() as session:
            rows = session.execute(select(Prediction)).scalars().all()
            assert len(rows) == 1

    def test_different_dates_are_separate_rows(self, temp_db):
        _seed_ticker("TEST.NS")
        result = _fake_result()
        record_prediction("TEST.NS", date(2026, 7, 20), result)
        record_prediction("TEST.NS", date(2026, 7, 21), result)
        with get_session() as session:
            rows = session.execute(select(Prediction)).scalars().all()
            assert len(rows) == 2

    def test_unknown_ticker_is_skipped_not_a_crash(self, temp_db):
        result = _fake_result()
        inserted = record_prediction("NEVER_SEEDED.NS", date(2026, 7, 20), result)
        assert inserted is False

    def test_rejects_a_result_with_no_prediction(self, temp_db):
        from datetime import datetime, timezone

        empty_result = PredictionResult(symbol="TEST.NS", generated_at=datetime.now(timezone.utc))
        with pytest.raises(ValueError, match="wasn't actually made"):
            record_prediction("TEST.NS", date(2026, 7, 20), empty_result)


class TestResolvePendingOutcomes:
    def _seed_price_history(self, symbol: str, closes: dict[date, float]):
        import core.queries as queries_module

        df = pd.DataFrame({"close": list(closes.values())}, index=pd.to_datetime(list(closes.keys())))

        def _fake_get_price_history(sym):
            return df if sym == symbol else pd.DataFrame()

        return _fake_get_price_history

    def test_resolves_a_prediction_once_real_prices_exist(self, temp_db, monkeypatch):
        _seed_ticker("TEST.NS")
        result = _fake_result(prob_up=0.6)  # predicts UP
        record_prediction("TEST.NS", date(2026, 7, 21), result)

        fake_history = self._seed_price_history("TEST.NS", {date(2026, 7, 20): 100.0, date(2026, 7, 21): 105.0})
        monkeypatch.setattr("core.ml.prediction_tracking.get_price_history", fake_history)
        monkeypatch.setattr("core.ml.prediction_tracking.previous_trading_day", lambda d: date(2026, 7, 20))

        resolved = resolve_pending_outcomes("TEST.NS")
        assert resolved == 1

        with get_session() as session:
            row = session.execute(select(Prediction)).scalars().one()
            assert row.actual_direction == 1  # 105 > 100 -> actually went up
            assert row.resolved_at is not None

    def test_does_not_resolve_when_target_date_price_not_yet_available(self, temp_db, monkeypatch):
        _seed_ticker("TEST.NS")
        result = _fake_result()
        record_prediction("TEST.NS", date(2026, 7, 25), result)  # far future, no price yet

        fake_history = self._seed_price_history("TEST.NS", {date(2026, 7, 20): 100.0, date(2026, 7, 21): 105.0})
        monkeypatch.setattr("core.ml.prediction_tracking.get_price_history", fake_history)
        monkeypatch.setattr("core.ml.prediction_tracking.previous_trading_day", lambda d: date(2026, 7, 24))

        resolved = resolve_pending_outcomes("TEST.NS")
        assert resolved == 0
        with get_session() as session:
            row = session.execute(select(Prediction)).scalars().one()
            assert row.actual_direction is None

    def test_a_down_move_resolves_to_actual_direction_zero(self, temp_db, monkeypatch):
        _seed_ticker("TEST.NS")
        result = _fake_result(prob_up=0.3)  # predicts DOWN
        record_prediction("TEST.NS", date(2026, 7, 21), result)

        fake_history = self._seed_price_history("TEST.NS", {date(2026, 7, 20): 100.0, date(2026, 7, 21): 95.0})
        monkeypatch.setattr("core.ml.prediction_tracking.get_price_history", fake_history)
        monkeypatch.setattr("core.ml.prediction_tracking.previous_trading_day", lambda d: date(2026, 7, 20))

        resolve_pending_outcomes("TEST.NS")
        with get_session() as session:
            row = session.execute(select(Prediction)).scalars().one()
            assert row.actual_direction == 0
            assert row.predicted_direction == 0
            assert row.actual_direction == row.predicted_direction  # this one was correct

    def test_no_pending_rows_returns_zero_without_error(self, temp_db):
        assert resolve_pending_outcomes() == 0
