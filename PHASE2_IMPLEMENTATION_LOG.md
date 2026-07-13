# Phase 2 Implementation Log — ML Foundation Improvements

Spec: `../docs/FINSIGHT_PHASE1_PHASE2_AGENT_SPEC.md` §9 (v2.3), plus the separate
Phase 2 Production v2.0 directive given 2026-07-13 (implementation order below follows
that directive, which is more granular than the original spec's §9.1–9.9).

Phase 1 is frozen per this directive — treat `core/database.py`, `core/symbol_registry.py`,
`core/checkpoint.py`, historical ingestion, and the DB architecture as read-only except
for minimal, justified, backward-compatible extensions.

This file is the single authoritative resume record — read it first in a new session;
do not restart a step marked Complete.

## Baseline (before Phase 2 work started)

- Git: `finsight` repo, `master`, HEAD `d2b01aa` (Phase 1 Completion Report), working tree clean.
- Tests: 456 passed.
- Phase 1 Acceptance Gate: NOT PASSED (Steps 6/7/9 blocked; Item 7 validation findings unresolved) — noted, not blocking Phase 2 per the user's explicit directive to proceed with Phase 2 despite this.

## Repository Evidence Map (Step 0 — Reconnaissance)

| Component needed | Reuse (evidence) | Extend | New |
|---|---|---|---|
| Label generation | `core.ml_model.build_labels` (next-day binary direction, used by both `core.ml_model.make_dataset` and `core.ml.feature_pipeline.make_dataset_v2`) | — | `core/ml/labels.py`: fixed-horizon, ATR-adjusted, volatility-adjusted, threshold-based, triple-barrier, meta-labeling candidates |
| Feature engineering | `core.ml_model.build_features` (9 features), `core.ml.feature_pipeline.build_features_v2` (27 features, extends the 9) | Extend `build_features_v2` further (Steps 2-5) or add a `build_features_v3` — decide per Step 2's own reconnaissance | Sector-relative (Step 3), market breadth (Step 4, symbol-independent), additional volatility (Step 5) |
| Feature store | `core.ml.feature_pipeline.persist_feature_set`/`load_feature_set` (`MLFeatureSet`/`MLFeatureValue`) | Reuse as-is for any new feature version | — |
| Dataset registry | `core.ml.data_layer.create_dataset_version`/`MLDatasetVersion` (Phase 1-extended with `internal_id`) | Reuse as-is | — |
| CV / walk-forward | `core.ml.cv.chronological_train_val_test_split`, `time_series_cv_folds`, `assert_no_chronological_leakage` (expanding-window, already built) | Extend with rolling-window walk-forward (Step 8) and `TimeSeriesSplit`/nested CV wrapper (Step 9) | — |
| Naive baseline | `core.ml.baseline.naive_persistence_predictions`/`naive_baseline_metrics` | Reuse for every benchmark comparison | — |
| Training | `core.ml.training.tune_model_family`/`tune_all_families` (Optuna, 4 families), `MLTrainingRun` logging | Reuse; may need a labels-parameter threading change | — |
| Generalization gate | `core.ml.generalization.evaluate_generalization`, `audit_feature_leakage` | Reuse as the leakage-prevention verification Step 8 requires ("verify explicitly, not by assertion") | — |
| Corrective actions | `core.ml.corrective_actions.attempt_corrections`, `select_features_by_correlation` | Reuse; Step 6 (Feature Selection) extends with mutual info/permutation/SHAP/stability | Feature Registry (deprecation-with-evidence, doesn't exist yet) |
| Evaluation | `core.ml.evaluation.generate_full_evaluation` (confusion matrix, learning curve, feature importance, SHAP) | Extend for Step 10 (drift tracking, alerting) and Step 12 (full 5-category benchmark) | — |
| Calibration | none | — | Step 7: Platt/isotonic/temperature scaling, Brier/ECE/MCE |
| Model registry | `core.ml.registry.register_model`/`get_active_model` (bare-filename artifact path, lineage) | Reuse for Step 11's model versioning and Step-12's promotion decisions | — |
| Improvement loop | `core.ml.improvement_loop.evaluate_keep_decision`/`log_iteration`, `MLImprovementIteration` | Reuse the keep/revert pattern for the Model Promotion Rule (statistical superiority, not just numeric) | — |
| Experiment tracking | `MLTrainingRun` (per-trial, not full per spec's Step 11 field list: no calibration/feature-importance/prediction-latency/notes columns yet) | Extend additively, or new `MLExperiment` table if `MLTrainingRun`'s shape is a poor fit — decide in Step 11 | Possibly a dedicated experiment table |
| Trading metrics | `core.portfolio.sharpe_ratio`, `max_drawdown` | Reuse for Step 12's Sharpe/Max Drawdown | Sortino, Calmar, Profit Factor, Win Rate, Turnover (none exist) |
| Volatility indicators | `core.indicators.atr`, `volatility`, `true_range` | Reuse for Step 5 (ATR already there); add Parkinson, Yang-Zhang, percentile, regime classification | Parkinson/Yang-Zhang volatility estimators |
| Sector data | `core.database.Ticker.sector` (from yfinance, already populated) | Reuse as the "never hardcode sector mappings" source for Step 3 | — |
| Inference | `core.ml_model.predict_next_direction` (registry-first, graceful fallback) | Extend once a Phase 2 champion is promoted (Model Promotion Rule) | — |

## Step Log

| Step | Status | Files | Tests | Commit |
|---|---|---|---|---|
| 1. Better Labels | **Implemented; selection PROVISIONAL pending Step 12** | `core/ml/labels.py` (new), `tests/test_ml_labels.py` (new, 16) | 16 new, all passing | see below |
| 2. Rolling Feature Engineering | Pending | | | |
| 3. Sector-Relative Features | Pending | | | |
| 4. Market Breadth | Pending | | | |
| 5. Volatility Features | Pending | | | |
| 6. Feature Selection | Pending | | | |
| 7. Probability Calibration | Pending | | | |
| 8. Walk-Forward Validation | Pending | | | |
| 9. Time-Series Cross-Validation | Pending | | | |
| 10. Feature Importance Monitoring | Pending | | | |
| 11. Experiment Tracking | Pending | | | |
| 12. Benchmarking | Pending | | | |

## Evidence — Step 1

- **Reuse:** `core.indicators.atr` (ATR-adjusted labels), `core.ml.feature_pipeline
  .build_features_v2` (comparison harness features) — no indicator math duplicated.
  `core.ml_model.build_labels` itself is untouched; `fixed_horizon_labels(horizon=1)`
  is proven byte-identical to it via `pd.testing.assert_series_equal`, not just
  "close enough."
- **Real bug found in a test, not production:** an off-by-one in my own
  "does not use data beyond the horizon window" test placed the out-of-window spike
  one day too early (at t+5, inside a horizon=5 window, not t+6 as intended). Fixed by
  adding one more warm-up day so the spike genuinely sits outside the window. The
  production `triple_barrier_labels` code was correct throughout — verified by the
  test passing once its own setup was fixed.
- **Tests:** 16 new (11 per-candidate correctness tests, 1 production-equivalence
  test, 3 meta-labeling tests, 1 end-to-end harness test on synthetic data), all
  passing.
- **Full suite: 472 passed** (456 + 16), 0 regressions.
- **Real preliminary comparison (RELIANCE.NS, 1,239 rows, single chronological 70/30
  split, RandomForest, no cross-validation yet — that's Steps 8/9):**

  | Candidate | Rows | Naive acc. | Model acc. | Precision | Recall | F1 |
  |---|---|---|---|---|---|---|
  | `fixed_horizon_1d` (= current production label) | 1174 | 0.5068 | 0.4929 | 0.4778 | 0.5029 | 0.4900 |
  | `fixed_horizon_5d` | 1170 | 0.5239 | 0.4644 | 0.4881 | 0.4457 | 0.4659 |
  | `atr_adjusted_1d` | 796 | 0.5025 | **0.5439** | 0.5146 | 0.4732 | 0.4930 |
  | `volatility_adjusted_1d` | 889 | 0.5084 | 0.4794 | 0.4675 | 0.5581 | 0.5088 |
  | `threshold_1pct_1d` | 469 | 0.5032 | **0.5816** | 0.6591 | 0.3973 | 0.4957 |
  | `triple_barrier_10d` | 789 | 0.5082 | **0.5359** | 0.5398 | 0.5126 | 0.5259 |

  **Honest reading, not a declared winner:** the current production label
  (`fixed_horizon_1d`) *underperforms* its own naive baseline in this single run,
  consistent with Phase 3's own documented finding that daily direction is
  near-random-walk. `atr_adjusted_1d`, `threshold_1pct_1d`, and `triple_barrier_10d`
  all beat their naive baselines here — but each also drops 30–62% of rows to the
  neutral class, shrinking the effective sample and making this single-split,
  single-symbol result weak evidence on its own. **Selection remains PROVISIONAL** —
  this needs Step 12's full benchmark suite (multi-symbol, walk-forward, calibration,
  trading metrics) before being treated as a real conclusion, per the directive's own
  text.

## Notes on sequencing vs. the directive's own text

Step 1 says "Select the winner using evidence from the Step 12 benchmark suite" — but
Step 12 doesn't exist until 11 steps later. Resolved as: Step 1 implements every
candidate label and runs a *preliminary* comparison using what already exists (naive
baseline, basic classification metrics via a chronological split) — the label choice is
marked **provisional** until Step 12's full benchmark suite exists, at which point it is
confirmed or revisited with full evidence. This is stated here as a real sequencing
tension in the directive, resolved via the "smallest safe interpretation," not silently
picked.
