# Model Comparison Report — Phase 2 Step 12

Two head-to-head comparisons were run this session, both using
`core.ml.benchmark.evaluate_model_promotion` (paired Wilcoxon signed-rank test on
matched per-fold ROC-AUC, plus calibration/walk-forward/latency gates). Full metric
definitions: `core/ml/benchmark.py`. Full numeric detail: `ML_BENCHMARK_REPORT.md`.

## Comparison 1: Feature Set — 9 original features vs. 34 Phase 2 features

**Setup:** Same symbol (RELIANCE.NS), same label (`fixed_horizon_1d`), same model
family (RandomForest), same 5 chronological folds. Only the feature set differs.

| Gate | Result |
|---|---|
| Statistically superior (paired Wilcoxon, α=0.05) | **No** — p=0.3125 |
| Calibration acceptable (ECE ≤ 0.15) | Yes (0.0426) |
| Walk-forward passed | Yes (5/5 valid folds) |
| Latency acceptable (≤ 200ms) | Yes (84.13ms) |
| **PROMOTE?** | **No — RETAIN CHAMPION** |

**Interpretation:** the 34-feature challenger wins on every trading and calibration
metric (see `ML_BENCHMARK_REPORT.md`'s full table) and has a numerically higher mean
ROC-AUC, but the difference across only 5 paired folds isn't statistically
distinguishable from noise. The directive's own rule governs this exact case:
*"If candidate and incumbent are statistically indistinguishable, retain the existing
production model. Simplicity and stability win ties."* Applied as written, not
overridden by the favorable *direction* of the other metrics.

## Comparison 2: Label Definition — `fixed_horizon_1d` vs. two alternatives

**Setup:** Same symbol, same 34-feature set, same model family, same 5 folds. Only the
label definition differs. This closes Step 1's explicitly deferred label selection.

| Challenger | p-value | Statistically superior? | Promote? |
|---|---|---|---|
| `atr_adjusted_1d` | 0.5938 | No | No — retain `fixed_horizon_1d` |
| `triple_barrier_10d` | 0.3125 | No | No — retain `fixed_horizon_1d` |

**Interpretation:** neither alternative label cleared the significance bar on ROC-AUC.
`fixed_horizon_1d` (matching current production) is retained. As with Comparison 1,
one alternative (`atr_adjusted_1d`) shows a much better calibration (ECE 0.0027 vs.
0.0426) and trading Sharpe (0.72 vs. -0.18) — flagged as a real, promising signal for
future work with a larger evaluation set, not acted on now given the current evidence
doesn't support statistical confidence.

## Why the same pattern in both comparisons?

Both real comparisons this session landed on "directionally better, not statistically
proven" at 5 folds on 1 symbol. This is a genuine, consistent finding about the
evidence available, not a coincidence to explain away: 5 paired observations is a small
sample for a significance test, and this project's own prior documentation
(`finsight/TRAINING_REPORT.md`, `finsight/SESSION_STATE.md`) already establishes that
daily NSE-equity direction prediction sits close to a random walk, where genuine edges
are small and require more data to distinguish from noise. Both retained-champion
decisions are the statistically honest outcome given that reality — not a failure of
the process.

## What would change this

Per `PHASE2_COMPLETION_REPORT.md`'s recommendations: re-run both comparisons across
more symbols and/or more folds before drawing a stronger conclusion either way. The
benchmark and promotion code is already built to support this without modification —
`run_full_benchmark`/`evaluate_model_promotion` take any features/labels/fold count.
