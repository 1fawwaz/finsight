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
| 2. Rolling Feature Engineering | **Complete** | `core/ml/feature_pipeline.py` (+`build_features_v3`/`make_dataset_v3`), `tests/test_ml_feature_pipeline.py` (+8) | 8 new, all passing | see below |
| 3. Sector-Relative Features | **Complete** | `core/ml/sector_features.py` (new), `tests/test_ml_sector_features.py` (new, 6) | 6 new, all passing | see below |
| 4. Market Breadth | **Complete** | `core/database.py` (+`MarketBreadthDaily`, new table), `core/ml/market_breadth.py` (new), `tests/test_ml_market_breadth.py` (new, 8) | 8 new, all passing | see below |
| 5. Volatility Features | **Complete** | `core/indicators.py` (extended: +5 functions), `tests/test_indicators.py` (+12) | 12 new, all passing | see below |
| 6. Feature Selection | **Complete** | `core/database.py` (+`FeatureRegistry`, new table), `core/ml/feature_selection.py` (new), `tests/test_ml_feature_selection.py` (new, 12) | 12 new, all passing | see below |
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

## Evidence — Step 2

- **Reuse, extend, no duplication:** `build_features_v3` calls `build_features_v2`
  directly and asserts (via test) the 27 original columns are byte-identical, not
  recomputed. Deliberately skipped adding volatility features here even though
  "volatility" is in Step 2's example list -- Step 5 owns that, and duplicating it in
  both steps would violate the "never duplicate feature engineering logic" rule.
- **7 new features, one per remaining named category:** `rolling_return_mean_10`
  (returns), `momentum_20` (momentum, second window alongside the existing
  `momentum_10`), `drawdown_20` (drawdown), `rolling_sharpe_20` (Sharpe),
  `price_zscore_20` (z-score), `return_autocorr_20` (correlation -- self-autocorrelation,
  since no second series exists at single-symbol level; cross-symbol correlation is
  Steps 3/4), `volume_percentile_20` (volume profile, a rank-based proxy).
- **Tests:** 8 new, including a no-lookahead regression test (same truncation-comparison
  pattern as the existing `build_features_v2` test), bounds checks
  (`drawdown_20 <= 0`, `volume_percentile_20 ∈ [0,1]`, `return_autocorr_20 ∈ [-1,1]`),
  and a hand-computable Sharpe sanity check (constant +1%/day returns produce a Sharpe
  > 10). All passing.
- **Full suite: 480 passed** (472 + 8), 0 regressions.
- **Real evidence:** built the full 34-feature set for RELIANCE.NS live data --
  correct shape, sensible real values (e.g. `volume_percentile_20` between 0.25–0.60
  over the last 3 real trading days).

## Evidence — Step 3

- **Sector mapping sourced from `core.database.Ticker.sector`** (yfinance-populated),
  never hardcoded, per the directive's explicit prohibition. Real sector composition
  checked against the live DB: Technology (TCS/INFY/WIPRO, 3 members), Financial
  Services (4 members), Energy/Consumer Cyclical/Utilities (2 members each), and four
  single-member sectors (Communication Services, Consumer Defensive, Industrials, Basic
  Materials) plus 3 sector-less benchmark indices.
- **`MIN_SECTOR_PEERS = 2`** is a real, tested design decision: a symbol with 0 or 1
  tracked peers gets NaN sector-relative features (an honest "unknown"), not a
  fabricated composite from itself or one other stock.
- **Test bug found and fixed, not production:** one test seeded only 1 peer for a
  feature that requires ≥2 by design, so it correctly returned NaN and the test's own
  assumption was wrong -- fixed by adding a second peer.
- **Tests:** 6 new, including a hand-computed excess-return equality check, all passing.
- **Full suite: 486 passed** (480 + 6), 0 regressions.
- **Real evidence:** `TCS.NS` (peers: `INFY.NS`, `WIPRO.NS`) shows real
  `relative_strength_vs_sector` ≈ 0.93 (recently underperforming its sector composite)
  and `sector_breadth` between 0.0–0.5. `BHARTIARTL.NS` (the real sole Communication
  Services stock in the tracked universe) correctly returns all-NaN, not a fabricated
  value.

## Evidence — Step 4

- **New table justified per the Architecture Change Rule** (stated, not assumed):
  `market_breadth_daily` is one row per trading date across the whole universe, which
  doesn't fit `MLFeatureValue`'s per-(feature_set, ticker, date) shape -- no existing
  table represents a market-wide daily fact. Additive; Phase 1 tables untouched.
- **Test bug found and fixed, not production:** a test asserted
  `value in (float("inf"), float("-inf"))`, which raises `TypeError: boolean value of NA
  is ambiguous` when the value is `pd.NA` -- exactly the correct, intended result for a
  zero-decline day (divide-by-zero avoided via `.replace(0, pd.NA)`). Fixed the
  assertion to use `pd.isna()` first.
- **Real deprecation warning fixed proactively:** `pct_change()`'s default
  `fill_method='pad'` is being removed in a future pandas version -- set explicitly to
  `fill_method=None` so gaps aren't silently forward-filled as if they were real trading.
- **Tests:** 8 new, all passing.
- **Full suite: 494 passed** (486 + 8), 0 regressions.
- **Migration evidence:** backup
  `data/backups/finsight_phase2_market_breadth_schema_migration_20260713_112751.db`,
  verified; new table confirmed via `sqlalchemy.inspect` (19→20 tables).
- **Real evidence:** computed and persisted market breadth across all 17 non-benchmark
  real tracked symbols -- 1,239 real trading dates written. Correctly shows
  `universe_size=1` for `2026-07-13` (only `RELIANCE.NS` has that day's candle ingested
  so far, per Step 14's evidence run) rather than fabricating full coverage for a date
  most symbols haven't synced yet -- an honest reflection of actual ingestion state.

## Evidence — Step 5

- **Reuse, extend, no duplication:** added directly to `core/indicators.py` (where
  `atr`/`volatility` already live) rather than a separate module -- `rolling_variance`
  is proven identical to `volatility()²`, not independently reimplemented.
- **Real production bug found and fixed:** `volatility_percentile`'s `min_periods`
  had a hardcoded floor of 20 regardless of the caller's `lookback`, so any
  `lookback < 20` raised `ValueError: min_periods must be <= window`. Fixed to scale
  `min_periods` with `lookback`, capped at `lookback` itself, so small windows degrade
  gracefully instead of crashing.
- **Test bug found and fixed, not production:** a Yang-Zhang test assumed this test
  file's `_make_ohlcv` helper produces an `"open"` column (a *different* helper of the
  same name in `test_ml_feature_pipeline.py` does; this one doesn't) -- fixed by
  constructing an `open_` series directly in the test.
- **5 new functions:** `rolling_variance`, `parkinson_volatility` (range-based),
  `yang_zhang_volatility` (overnight + open-close + Rogers-Satchell components --
  proven to react to overnight gaps that Parkinson is blind to, and to correctly
  return ~0 for a perfectly flat series), `volatility_percentile` (relative to the
  symbol's own history, not a hardcoded absolute level), `volatility_regime`
  (low/medium/high terciles).
- **Tests:** 12 new, including formula-level reference checks and two estimators'
  defining properties tested directly (Parkinson: wider range -> higher vol;
  Yang-Zhang: gap-sensitive where Parkinson isn't).
- **Full suite: 506 passed** (494 + 12), 0 regressions.
- **Real evidence:** computed all 5 estimators for RELIANCE.NS live data --
  close-to-close (~0.186), Parkinson (~0.158), Yang-Zhang all real and in a sensible
  range; `volatility_regime` correctly transitions low→medium over the last 5 real
  trading days as `vol_percentile` crosses the 0.33 tercile boundary.

## Evidence — Step 6

- **Reuse:** `core.ml.cv.time_series_cv_folds`/`assert_no_chronological_leakage` (Phase 3)
  for stability analysis -- no new CV logic. Gain-based importance and SHAP already
  exist in `core.ml.evaluation` (Phase 3) and are intentionally not re-derived here;
  Step 6 adds what didn't exist: mutual information, permutation importance,
  correlation redundancy, cross-fold stability, and the Feature Registry.
- **New table justified:** `feature_registry` — no existing table tracks feature
  lifecycle/deprecation decisions with evidence.
- **A real design lesson, not a threshold-tuning exercise:** the first version flagged
  "weak" features using an absolute permutation-importance threshold. Testing against a
  synthetic zero-signal feature showed permutation importance is too noisy for an
  absolute cutoff to be reliable (a genuinely useless feature scored 0.0064–0.0077
  across repeated runs — indistinguishable by magnitude from other noise features, no
  clean gap from zero). **Refactored** to `flag_weak_features`: MI still uses an
  absolute near-zero threshold (well-behaved there), but permutation importance is
  judged by *relative rank* (bottom 50% of the feature set) instead — robust to the
  same noise that broke the absolute-threshold version. The deterministic flagging
  logic is now unit-tested directly with hand-supplied values, separate from the noisy
  end-to-end statistical computation.
- **Tests:** 12 new, all passing.
- **Full suite: 518 passed** (506 + 12), 0 regressions.
- **Migration evidence:** backup
  `data/backups/finsight_phase2_feature_registry_schema_migration_20260713_114356.db`,
  verified; new table confirmed via `sqlalchemy.inspect`.
- **Real evidence, RELIANCE.NS's real 34-feature set (`build_features_v3`):** found 28
  correlated pairs above 0.9, including a genuine near-duplicate
  (`bollinger_pct_b`/`price_zscore_20` at correlation 1.0000 — both are z-score-like
  formulas, confirming the redundancy detector works on real, not just synthetic,
  data) and `roc_10`/`momentum_10`/`rolling_return_mean_10` all mutually >0.99
  correlated (three different formulations of "recent return," as expected). Flagged 7
  weak candidates (`sma_20_dist`, `volatility_20`, `ema_20_dist`,
  `dist_from_resistance`, `drawdown_20`, `momentum_20`, `rolling_sharpe_20`) --
  **not deprecated** (that needs a deliberate registry decision with evidence, per the
  directive; flagging is the evidence, not the decision itself).

## Notes on sequencing vs. the directive's own text

Step 1 says "Select the winner using evidence from the Step 12 benchmark suite" — but
Step 12 doesn't exist until 11 steps later. Resolved as: Step 1 implements every
candidate label and runs a *preliminary* comparison using what already exists (naive
baseline, basic classification metrics via a chronological split) — the label choice is
marked **provisional** until Step 12's full benchmark suite exists, at which point it is
confirmed or revisited with full evidence. This is stated here as a real sequencing
tension in the directive, resolved via the "smallest safe interpretation," not silently
picked.
