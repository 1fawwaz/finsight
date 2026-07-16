"""Tests for core/ml/performance.py -- accuracy/precision/recall/F1 aggregation over
real resolved predictions only. Uses temp_db to avoid touching the real finsight.db."""

from __future__ import annotations

from datetime import date, datetime, timezone

from core.database import Prediction, Ticker, get_session
from core.ml.performance import overall_performance, performance_by_confidence_bucket, performance_by_market_regime, prediction_timeline


def _seed_ticker(symbol: str) -> int:
    with get_session() as session:
        t = Ticker(symbol=symbol, name=symbol)
        session.add(t)
        session.flush()
        return t.id


def _seed_prediction(ticker_id, d, predicted, actual, confidence_level=None, market_regime=None, model_version="m_v1"):
    with get_session() as session:
        session.add(
            Prediction(
                ticker_id=ticker_id,
                date=d,
                model_version=model_version,
                predicted_direction=predicted,
                probability=0.6 if predicted == 1 else 0.4,
                actual_direction=actual,
                confidence_level=confidence_level,
                market_regime=market_regime,
                recorded_at=datetime.now(timezone.utc),
            )
        )
        session.flush()


class TestOverallPerformance:
    def test_no_resolved_rows_returns_none_stats_not_fabricated_zeros(self, temp_db):
        stats = overall_performance()
        assert stats.n == 0
        assert stats.accuracy is None
        assert stats.precision is None

    def test_all_correct_predictions_score_perfect_accuracy(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 1)
        _seed_prediction(tid, date(2026, 1, 2), 0, 0)
        _seed_prediction(tid, date(2026, 1, 3), 1, 1)
        stats = overall_performance()
        assert stats.n == 3
        assert stats.accuracy == 1.0

    def test_all_wrong_predictions_score_zero_accuracy(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 0)
        _seed_prediction(tid, date(2026, 1, 2), 0, 1)
        stats = overall_performance()
        assert stats.accuracy == 0.0

    def test_unresolved_predictions_are_excluded(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 1)
        _seed_prediction(tid, date(2026, 1, 2), 1, None)  # unresolved
        stats = overall_performance()
        assert stats.n == 1

    def test_filters_by_symbol(self, temp_db):
        tid_a = _seed_ticker("A.NS")
        tid_b = _seed_ticker("B.NS")
        _seed_prediction(tid_a, date(2026, 1, 1), 1, 1)
        _seed_prediction(tid_b, date(2026, 1, 1), 1, 0)
        stats_a = overall_performance(symbol="A.NS")
        assert stats_a.n == 1
        assert stats_a.accuracy == 1.0

    def test_filters_by_model_version(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 1, model_version="v1")
        _seed_prediction(tid, date(2026, 1, 2), 1, 0, model_version="v2")
        stats_v1 = overall_performance(model_version="v1")
        assert stats_v1.n == 1
        assert stats_v1.accuracy == 1.0


class TestPerformanceByConfidenceBucket:
    def test_buckets_split_correctly(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 1, confidence_level="High")
        _seed_prediction(tid, date(2026, 1, 2), 1, 0, confidence_level="High")
        _seed_prediction(tid, date(2026, 1, 3), 0, 0, confidence_level="Low")
        by_bucket = performance_by_confidence_bucket()
        assert set(by_bucket.keys()) == {"High", "Low"}
        assert by_bucket["High"].n == 2
        assert by_bucket["High"].accuracy == 0.5
        assert by_bucket["Low"].n == 1
        assert by_bucket["Low"].accuracy == 1.0

    def test_rows_without_a_confidence_level_are_excluded_not_a_fake_bucket(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 1, confidence_level=None)
        by_bucket = performance_by_confidence_bucket()
        assert by_bucket == {}

    def test_empty_predictions_table_returns_empty_dict(self, temp_db):
        assert performance_by_confidence_bucket() == {}


class TestPerformanceByMarketRegime:
    def test_regimes_split_correctly(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, 1, market_regime="Trending / Low Volatility")
        _seed_prediction(tid, date(2026, 1, 2), 1, 0, market_regime="Range-Bound / High Volatility")
        by_regime = performance_by_market_regime()
        assert set(by_regime.keys()) == {"Trending / Low Volatility", "Range-Bound / High Volatility"}
        assert by_regime["Trending / Low Volatility"].accuracy == 1.0


class TestPredictionTimeline:
    def test_unknown_symbol_returns_empty_list(self, temp_db):
        assert prediction_timeline("NEVER_SEEDED.NS") == []

    def test_includes_unresolved_rows_unlike_resolved_only_queries(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, actual=None)  # unresolved
        timeline = prediction_timeline("A.NS")
        assert len(timeline) == 1
        assert timeline[0]["actual_direction"] is None

    def test_newest_first(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, actual=1)
        _seed_prediction(tid, date(2026, 1, 3), 1, actual=None)
        _seed_prediction(tid, date(2026, 1, 2), 0, actual=0)
        timeline = prediction_timeline("A.NS")
        assert [row["date"] for row in timeline] == [date(2026, 1, 3), date(2026, 1, 2), date(2026, 1, 1)]

    def test_filters_by_model_version(self, temp_db):
        tid = _seed_ticker("A.NS")
        _seed_prediction(tid, date(2026, 1, 1), 1, actual=1, model_version="v1")
        _seed_prediction(tid, date(2026, 1, 2), 1, actual=1, model_version="v2")
        timeline = prediction_timeline("A.NS", model_version="v1")
        assert len(timeline) == 1
        assert timeline[0]["model_version"] == "v1"

    def test_respects_limit(self, temp_db):
        tid = _seed_ticker("A.NS")
        for i in range(10):
            _seed_prediction(tid, date(2026, 1, 1 + i), 1, actual=1)
        timeline = prediction_timeline("A.NS", limit=3)
        assert len(timeline) == 3
