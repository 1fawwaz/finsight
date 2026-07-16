"""Tests for core/ml/confidence.py -- confidence scoring/banding is derived purely from
a given probability, never hardcoded."""

from __future__ import annotations

import pytest

from core.ml.confidence import (
    CONFIDENCE_LEVELS,
    assess_confidence,
    confidence_level_from_score,
    confidence_score_from_probability,
)


class TestConfidenceScoreFromProbability:
    def test_a_coin_flip_probability_scores_zero(self):
        assert confidence_score_from_probability(0.5) == 0.0

    def test_certainty_up_scores_100(self):
        assert confidence_score_from_probability(1.0) == pytest.approx(100.0)

    def test_certainty_down_scores_100(self):
        assert confidence_score_from_probability(0.0) == pytest.approx(100.0)

    def test_symmetric_around_half(self):
        assert confidence_score_from_probability(0.7) == pytest.approx(confidence_score_from_probability(0.3))


class TestConfidenceLevelFromScore:
    @pytest.mark.parametrize(
        "score,expected_level",
        [(0.0, "Very Low"), (10.0, "Very Low"), (15.0, "Low"), (34.9, "Low"), (35.0, "Medium"), (59.9, "Medium"), (60.0, "High"), (79.9, "High"), (80.0, "Very High"), (100.0, "Very High")],
    )
    def test_thresholds(self, score, expected_level):
        assert confidence_level_from_score(score) == expected_level

    def test_every_level_is_reachable(self):
        seen = {confidence_level_from_score(s) for s in [0, 20, 40, 65, 90]}
        assert seen == set(CONFIDENCE_LEVELS)


class TestAssessConfidence:
    def test_returns_complementary_probabilities(self):
        assessment = assess_confidence(0.7, was_calibrated=True)
        assert assessment.probability_up == pytest.approx(0.7)
        assert assessment.probability_down == pytest.approx(0.3)

    def test_prediction_class_up_at_or_above_half(self):
        assert assess_confidence(0.5, was_calibrated=True).prediction_class == "UP"
        assert assess_confidence(0.51, was_calibrated=True).prediction_class == "UP"

    def test_prediction_class_down_below_half(self):
        assert assess_confidence(0.49, was_calibrated=True).prediction_class == "DOWN"

    def test_was_calibrated_flag_is_passed_through_not_inferred(self):
        assert assess_confidence(0.6, was_calibrated=False).was_calibrated is False
        assert assess_confidence(0.6, was_calibrated=True).was_calibrated is True

    def test_clamps_out_of_range_probability_rather_than_crashing(self):
        # A defensive guard, not an expected input -- predict_proba should never
        # return outside [0, 1], but a corrupted/adversarial model output must not
        # crash the app either (see the task's "no prediction should crash the
        # application" requirement).
        assessment = assess_confidence(1.5, was_calibrated=True)
        assert assessment.probability_up == 1.0
        assessment2 = assess_confidence(-0.5, was_calibrated=True)
        assert assessment2.probability_up == 0.0
