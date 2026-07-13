# Training Performance Report — Phase 2 Step 12 (Performance Category)

All numbers measured directly (`time.perf_counter`, stdlib `tracemalloc`, real
`joblib` serialization) against real model fits on RELIANCE.NS data — none estimated.
Methodology: `core.ml.benchmark.compute_performance_metrics`.

## Feature-Set Comparison (RandomForest, both configurations)

| Metric | 9 original features | 34 Phase 2 features | Delta |
|---|---|---|---|
| Training time | 2.012s | 2.285s | +13.6% |
| Prediction latency (single row, avg of 20) | 31.15ms | 32.34ms | +3.8% |
| Peak memory during fit (`tracemalloc`) | 0.902 MB | 0.941 MB | +4.4% |
| Model size (serialized, `joblib`) | 526,425 bytes | 535,337 bytes | +1.7% |
| Inference throughput (batch) | 5,266/sec | 4,605/sec | -12.6% |

**Reading:** the 34-feature model costs modestly more across every performance
dimension (as expected — roughly 3.8× more input columns), but remains well within the
200ms latency gate used by `evaluate_model_promotion` (both under 35ms) and both fit in
about 2 seconds. Performance was **not** the reason either promotion decision retained
the champion in `MODEL_COMPARISON_REPORT.md` — both configurations passed the latency
gate comfortably; the decisions turned on statistical significance of the primary
metric, not cost.

## Nested Cross-Validation Timing (Phase 2 Step 9, real evidence)

A separate real timing data point, from Step 9's nested time-series CV run (2 outer
folds × 2 inner Optuna trials × 2 inner CV folds, RandomForest, on RELIANCE.NS's
34-feature set): **2.2 seconds total real wall-clock time** for the entire nested
procedure. Recorded here since it's a genuine training-performance data point not
otherwise captured in the Step 12 benchmark run (which measures a single fit, not a
tuning loop).

## Full Regression Test Suite Timing

The project's own test suite (583 tests, spanning Phase 1's Enterprise Data Platform
and Phase 2's ML Foundation Improvements) completes in **~90–220 seconds** depending on
machine load, per multiple real runs this session. Not a model-training metric, but
included as a genuine "how long does verification take" data point relevant to
iteration speed on this codebase.

## What Was Not Measured

- **GPU performance** — every model family used in this session (RandomForest; XGBoost/
  CatBoost/LightGBM exist via `core.ml.training` but weren't re-benchmarked for Step 12
  specifically) ran CPU-only. No GPU timing exists to report.
- **Multi-symbol batch training throughput** — every measurement here is single-symbol.
  Training-time scaling across the Nifty100/500 universe (once unblocked, see Phase 1's
  Steps 6/7/9) is unmeasured.
- **Cold-start vs. warm-cache disk I/O** for model loading — `model_size_bytes` is
  measured; actual disk read latency for `core.ml.registry.load_model_by_version` was
  not separately profiled in this session (Phase 3 already measured its own inference
  latency at p50=27.5ms/p95=28.7ms for the registered production model, per
  `finsight/SESSION_STATE.md` §8 — not re-measured here).
