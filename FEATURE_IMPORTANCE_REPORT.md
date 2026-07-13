# Feature Importance Report — Phase 2 Steps 6 & 10

Methodology: `core/ml/feature_selection.py` (mutual information, permutation
importance, correlation redundancy, cross-fold stability) and
`core/ml/feature_importance_monitoring.py` (persistence + drift detection). Gain-based
and SHAP importance reuse Phase 3's `core.ml.evaluation` unchanged.

## Step 6 — Full Evaluation (RELIANCE.NS, real 34-feature set)

**Top 5 by mutual information:**

| Feature | MI |
|---|---|
| `upper_wick_pct` | 0.02592 |
| `volume_ratio_5_20` | 0.02357 |
| `bollinger_pct_b` | 0.01938 |
| `price_zscore_20` | 0.01938 |
| `dist_from_52w_low` | 0.01880 |

**Top 5 by permutation importance:**

| Feature | Permutation importance |
|---|---|
| `gap_pct` | 0.02504 |
| `return_autocorr_20` | 0.02317 |
| `lower_wick_pct` | 0.02104 |
| `bollinger_bandwidth` | 0.02104 |
| `volume_zscore` | 0.01891 |

**Correlation redundancy:** 28 pairs found above 0.9, including:

- `bollinger_pct_b` / `price_zscore_20` — **correlation 1.0000** (both are z-score-like
  formulas over a 20-day window; this is why they tie for identical MI above).
- `roc_10` / `rolling_return_mean_10` — 0.9995
- `momentum_10` / `rolling_return_mean_10` — 0.9946
- `roc_10` / `momentum_10` — 0.9942
- `price_to_vwap` / `sma_20_dist` — 0.9942

**Weak feature candidates flagged** (low MI *and* bottom-half permutation rank —
requiring agreement between both methods, per Step 6's design): `sma_20_dist`,
`volatility_20`, `ema_20_dist`, `dist_from_resistance`, `drawdown_20`, `momentum_20`,
`rolling_sharpe_20`.

## Registry Action Taken (real, not hypothetical)

| Feature | Status | Reason |
|---|---|---|
| `price_zscore_20` | **Deprecated** | Correlation 1.0000 with `bollinger_pct_b` (above). Redundant; `bollinger_pct_b` predates it (Phase 3) and is retained. |
| `bollinger_pct_b` | Active | Retained over its near-duplicate, see above. |

This is the only deprecation decision made this session — the 7 features flagged as
"weak" above were **not** deprecated; they remain active pending further evidence, per
the directive's "never silently remove" rule. Deprecating a feature requires a specific,
individually-justified reason (as `price_zscore_20`'s clean 1.0000-correlation case
provided), not a blanket action on every flagged candidate.

## Step 10 — Drift Monitoring: a real, unresolved finding

Two snapshots were recorded for RELIANCE.NS (mutual information on a 600-row subset vs.
the full 1,174-row set) and compared at the default 50% relative-change threshold:
**all 34/34 features were flagged as significant drift.**

This is **not treated as validated evidence of anything wrong with these features** —
it's a calibration finding about the *monitoring threshold itself*: importance measures
are substantially noisy at this data scale (consistent with Step 6's own permutation-
importance-noise finding), and comparing two very differently-sized data slices is a
more dramatic shift than a realistic same-model, adjacent-time-period comparison would
produce. **Open item, not resolved:** which importance type (permutation/SHAP/gain) and
threshold are actually reliable for real alerting is left for future work, stated
explicitly in `PHASE2_IMPLEMENTATION_LOG.md` rather than quietly tuned to look
reassuring.

## Reproducibility

```python
from core.ml.feature_selection import evaluate_features
from core.ml.feature_importance_monitoring import record_importance_snapshot, detect_all_drift
# report = evaluate_features(features, labels)
# report.mutual_information, report.permutation_importance, report.correlated_pairs,
# report.stability, report.weak_feature_candidates
```
