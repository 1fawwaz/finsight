"""Phase 3 Step 2.9: Autonomous Improvement Loop.

Target metric: ROC-AUC on the held-out, chronologically-final fold (the test split
from core.ml.cv.chronological_train_val_test_split) -- the same metric declared and
used throughout core.ml.training and core.ml.generalization. Declared once here,
never redefined between iterations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.config import get_logger
from core.database import MLImprovementIteration, get_session

logger = get_logger(__name__)

TARGET_METRIC = "roc_auc"
IMPROVEMENT_THRESHOLD_PCT = 0.5  # spec default: keep only if relative improvement exceeds this
REGRESSION_THRESHOLD_PCT = 1.0  # spec default: no other metric may regress beyond this, relative
MAX_ITERATIONS = 20
CONSECUTIVE_NON_IMPROVING_STOP = 3


@dataclass
class KeepDecision:
    keep: bool
    relative_improvement_pct: float
    regressions: list[str]
    reasoning: str


def evaluate_keep_decision(
    metric_before: float,
    metric_after: float,
    secondary_before: dict,
    secondary_after: dict,
    new_test_failures: bool,
    new_gate_flag_triggered: bool,
) -> KeepDecision:
    """The spec's keep/revert rule, applied mechanically to real numbers: improvement
    > 0.5% relative on the target metric, no other metric regresses beyond 1% relative,
    no new test failures, no newly-triggered 2.3.1 flag."""
    relative_improvement = ((metric_after - metric_before) / metric_before * 100) if metric_before else 0.0

    regressions = []
    for key, before_val in secondary_before.items():
        if key not in secondary_after or before_val == 0:
            continue
        after_val = secondary_after[key]
        rel_change = (after_val - before_val) / abs(before_val) * 100
        if rel_change < -REGRESSION_THRESHOLD_PCT:
            regressions.append(f"{key}: {before_val:.4f} -> {after_val:.4f} ({rel_change:+.1f}%)")

    meets_threshold = relative_improvement > IMPROVEMENT_THRESHOLD_PCT
    no_regressions = len(regressions) == 0
    keep = meets_threshold and no_regressions and not new_test_failures and not new_gate_flag_triggered

    reasoning = (
        f"{TARGET_METRIC} relative improvement={relative_improvement:.2f}% (threshold >{IMPROVEMENT_THRESHOLD_PCT}%) "
        f"-> meets_threshold={meets_threshold}. secondary-metric regressions={regressions or 'none'} -> "
        f"no_regressions={no_regressions}. new_test_failures={new_test_failures}. "
        f"new_gate_flag_triggered={new_gate_flag_triggered}. DECISION: {'KEEP' if keep else 'REVERT'}."
    )
    return KeepDecision(keep=keep, relative_improvement_pct=relative_improvement, regressions=regressions, reasoning=reasoning)


def log_iteration(
    iteration_number: int,
    change_description: str,
    hypothesis: str,
    metric_before: float,
    metric_after: float,
    secondary_before: dict,
    secondary_after: dict,
    test_results: str,
    decision: KeepDecision,
) -> None:
    """Persist one iteration -- kept or reverted, every one logged, none deleted."""
    with get_session() as session:
        session.add(
            MLImprovementIteration(
                iteration_number=iteration_number,
                change_description=change_description,
                hypothesis=hypothesis,
                metric_name=TARGET_METRIC,
                metric_before=metric_before,
                metric_after=metric_after,
                secondary_metrics_json=json.dumps({"before": secondary_before, "after": secondary_after}),
                relative_improvement_pct=decision.relative_improvement_pct,
                test_results=test_results,
                regression_check=("; ".join(decision.regressions) if decision.regressions else "no regressions"),
                kept=decision.keep,
            )
        )
    logger.info("Iteration %d logged: %s", iteration_number, decision.reasoning)
