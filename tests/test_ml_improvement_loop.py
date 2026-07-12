"""Tests for core.ml.improvement_loop: the keep/revert decision rule and iteration logging."""

import pytest

from core.database import MLImprovementIteration, get_session
from core.ml.improvement_loop import evaluate_keep_decision, log_iteration


def test_keep_decision_keeps_a_real_improvement_with_no_regressions():
    decision = evaluate_keep_decision(
        metric_before=0.500, metric_after=0.520,
        secondary_before={"accuracy": 0.50}, secondary_after={"accuracy": 0.51},
        new_test_failures=False, new_gate_flag_triggered=False,
    )
    assert decision.keep is True
    assert decision.relative_improvement_pct == pytest.approx(4.0, rel=1e-2)


def test_keep_decision_reverts_when_improvement_below_threshold():
    decision = evaluate_keep_decision(
        metric_before=0.500, metric_after=0.5015,  # 0.3% relative, below the 0.5% threshold
        secondary_before={}, secondary_after={},
        new_test_failures=False, new_gate_flag_triggered=False,
    )
    assert decision.keep is False


def test_keep_decision_reverts_on_secondary_metric_regression_even_with_good_primary_improvement():
    decision = evaluate_keep_decision(
        metric_before=0.500, metric_after=0.520,  # 4% improvement on target metric
        secondary_before={"precision": 0.50}, secondary_after={"precision": 0.45},  # -10% regression
        new_test_failures=False, new_gate_flag_triggered=False,
    )
    assert decision.keep is False
    assert decision.regressions


def test_keep_decision_reverts_on_new_test_failures():
    decision = evaluate_keep_decision(
        metric_before=0.500, metric_after=0.530,
        secondary_before={}, secondary_after={},
        new_test_failures=True, new_gate_flag_triggered=False,
    )
    assert decision.keep is False


def test_keep_decision_reverts_on_new_gate_flag():
    decision = evaluate_keep_decision(
        metric_before=0.500, metric_after=0.530,
        secondary_before={}, secondary_after={},
        new_test_failures=False, new_gate_flag_triggered=True,
    )
    assert decision.keep is False


def test_log_iteration_persists_kept_and_reverted_iterations(temp_db):
    kept_decision = evaluate_keep_decision(0.50, 0.52, {}, {}, False, False)
    log_iteration(1, "tried X", "hypothesis X", 0.50, 0.52, {}, {}, "10 passed", kept_decision)

    reverted_decision = evaluate_keep_decision(0.52, 0.515, {}, {}, False, False)
    log_iteration(2, "tried Y", "hypothesis Y", 0.52, 0.515, {}, {}, "10 passed", reverted_decision)

    with get_session() as session:
        rows = session.query(MLImprovementIteration).order_by(MLImprovementIteration.iteration_number).all()
    assert len(rows) == 2
    assert rows[0].kept is True
    assert rows[1].kept is False
    assert rows[0].iteration_number == 1
    assert rows[1].iteration_number == 2
