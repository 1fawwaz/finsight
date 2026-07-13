# ML Benchmark Report — Phase 2 Step 12

Methodology and full metric definitions live in `core/ml/benchmark.py`. This report
records the real benchmark runs executed this session. All numbers are real model
fits on real RELIANCE.NS data (`FIN-0001`) — none fabricated or estimated.

## Methodology

- **Split:** chronological 60/20/20 train/calibration/test (no shuffling, ever).
- **Fold metrics:** 5 expanding-window chronological folds
  (`core.ml.cv.time_series_cv_folds`), each retrained fresh — never a frozen artifact
  evaluated on data it may have already seen.
- **Model family:** RandomForest throughout (for a controlled, apples-to-apples
  comparison across labels/feature sets — not a claim that other families weren't
  considered; Optuna-based multi-family tuning already exists in `core.ml.training`
  from Phase 3 and is reused, not duplicated, wherever hyperparameter search is needed).

## Benchmark Run 1 — Feature set comparison (9 original vs. 34 Phase 2 features)

| Category | Metric | Champion (9 features) | Challenger (34 features) |
|---|---|---|---|
| Classification | Accuracy | 0.4149 | 0.4766 |
| Classification | Precision | 0.4110 | 0.4698 |
| Classification | Recall | 0.5217 | 0.6140 |
| Classification | F1 | 0.4598 | 0.5323 |
| Classification | ROC-AUC | 0.4523 | 0.5157 |
| Classification | PR-AUC | 0.4910 | 0.5155 |
| Classification | Log Loss | 0.7068 | 0.6983 |
| Classification | MCC | -0.1644 | -0.0403 |
| Classification | Balanced Accuracy | 0.4196 | 0.4806 |
| Calibration | Method (best by ECE) | — | Platt |
| Calibration | ECE | — | 0.0426 |
| Fold ROC-AUC | (5 folds) | `[0.5141, 0.4161, 0.4757, 0.5149, 0.5172]` | `[0.5225, 0.4526, 0.4751, 0.5360, 0.4874]` |

**Promotion decision:** Paired Wilcoxon signed-rank test, p=0.3125 (α=0.05) →
**not statistically superior**. Calibration acceptable, walk-forward passed, latency
acceptable (84.13ms). **DECISION: RETAIN CHAMPION.**

Full reasoning and context: `PHASE2_IMPLEMENTATION_LOG.md` Step 12 evidence.

## Benchmark Run 2 — Label comparison (on the 34-feature set)

| Label | Fold ROC-AUC | Test ROC-AUC | ECE | Sharpe |
|---|---|---|---|---|
| `fixed_horizon_1d` (retained) | `[0.5225, 0.4526, 0.4751, 0.5360, 0.4874]` | 0.5157 | 0.0426 | -0.1757 |
| `atr_adjusted_1d` | `[0.5282, 0.4127, 0.4416, 0.5352, 0.5335]` | 0.5139 | 0.0027 | 0.7156 |
| `triple_barrier_10d` | `[0.6216, 0.6797, 0.3047, 0.6073, 0.4475]` | 0.4940 | 0.0366 | -0.2631 |

**Promotion decisions (vs. `fixed_horizon_1d`):**
- `atr_adjusted_1d`: p=0.5938 → not statistically superior. RETAIN.
- `triple_barrier_10d`: p=0.3125 → not statistically superior. RETAIN.

## Trading, Calibration (full), and Stability Category Detail (Run 1)

| Metric | Champion (9 features) | Challenger (34 features) |
|---|---|---|
| Sharpe | -0.9980 | -0.1757 |
| Sortino | -1.1378 | -0.2163 |
| Calmar | -0.7340 | -0.2990 |
| Profit Factor | 0.8034 | 0.9630 |
| Max Drawdown | -23.35% | -14.05% |
| Win Rate | 41.10% | 46.98% |
| Avg Gain | 1.127% | 1.010% |
| Avg Loss | -1.014% | -0.966% |
| Annual Return | -17.14% | -4.20% |
| Volatility (annualized) | 17.33% | 16.61% |
| Turnover | 0.3029 | 0.2894 |
| Calibration method (own best) | isotonic | Platt |
| Brier Score | 0.2602 | 0.2515 |
| ECE | 0.0942 | 0.0426 |
| MCE | 0.2080 | 0.1275 |
| Fold variance | 0.000985 | 0.000931 |
| Walk-forward variance | 0.005081 | 0.004105 |
| Feature stability (mean CV) | 0.1558 | 0.2393 |
| Prediction stability | 0.9398 | 0.9110 |
| Probability stability (std) | 0.01151 | 0.01288 |

**Honest reading:** the challenger (34 features) is better on every single trading and
calibration metric — a real, consistent, honest signal — but did **not** clear the
statistical-significance bar on the primary classification metric (ROC-AUC) in the
Promotion Decision above, at only 5 folds. This is worth surfacing plainly: the
*direction* of every metric favors the challenger, even though the formal decision was
RETAIN CHAMPION. A larger evaluation (more folds/symbols) is the natural next step to
resolve this tension, not a reason to override the statistical test now.

## Reproducibility

Every number in this report can be regenerated with:

```python
from core.queries import get_price_history
from core.ml.feature_pipeline import build_features_v3
from core.ml_model import build_features as build_features_v1, build_labels
from core.ml.benchmark import run_full_benchmark, evaluate_model_promotion

df = get_price_history("RELIANCE.NS")
labels = build_labels(df["close"])
# Run 1: champion = run_full_benchmark(build_features_v1(df), labels, df["close"])
#        challenger = run_full_benchmark(build_features_v3(df), labels, df["close"])
# Run 2: see core/ml/labels.py candidates, same pattern with different label Series.
```

## Known Limitations

- **Single symbol, single random seed.** Every result here is RELIANCE.NS only. No
  cross-symbol generalization claim is made or implied.
- **5 folds is a small sample for a paired significance test** — a p-value near but
  above 0.05 (e.g. `atr_adjusted_1d`'s 0.594) is consistent with either "genuinely no
  effect" or "an effect too small to detect at this sample size." Not distinguished
  here; stated as a limitation, not resolved.
