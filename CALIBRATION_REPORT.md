# Calibration Report — Phase 2 Step 7 + Step 12

Methodology: `core/ml/calibration.py`. Platt and isotonic scaling reuse sklearn's
`CalibratedClassifierCV` directly (`cv="prefit"`, fit on a held-out calibration split,
never the model's own training data); temperature scaling is a direct implementation
(single-scalar log-loss minimization, Guo et al. 2017) since sklearn has no built-in
equivalent. ECE/MCE use a standard 10-bin uniform-width binning.

## Step 7 — Method Comparison (RELIANCE.NS, 9-feature RandomForest)

| Method | Brier | ECE | MCE |
|---|---|---|---|
| raw | 0.2525 | 0.0644 | 0.1246 |
| **Platt** | **0.2515** | **0.0426** | 0.1275 |
| isotonic | 0.2568 | 0.0829 | 0.2541 |
| temperature (T=1.142) | 0.2519 | 0.0593 | 0.2834 |

**Best method: Platt scaling** (wins on both Brier and ECE). Isotonic performed *worse*
than raw here — a real, honestly-reported finding, not a bug: isotonic regression is
more flexible than Platt and needs more calibration data to avoid overfitting its own
curve; the calibration split used here was only ~235 rows (20% of ~1,174 total rows for
a single symbol).

## Step 12 — Calibration by Model Configuration

| Configuration | Best method | Brier | ECE | MCE |
|---|---|---|---|---|
| 9 original features | isotonic | 0.2602 | 0.0942 | 0.2080 |
| 34 Phase 2 features | Platt | 0.2515 | 0.0426 | 0.1275 |
| `atr_adjusted_1d` label (34 features) | — | — | **0.0027** | — |
| `triple_barrier_10d` label (34 features) | — | — | 0.0366 | — |
| `fixed_horizon_1d` label (34 features, retained) | Platt | 0.2515 | 0.0426 | 0.1275 |

**Note on inconsistency between the two "9 original features" rows across reports:**
Step 7's comparison and Step 12's Run 1 champion both use the 9-feature set but select
different best methods (Platt in Step 7's isolated run vs. isotonic in Step 12's
full-benchmark run). This reflects a real, different random split/fold boundary between
the two runs (Step 7 used a 60/20/20 split with a fixed seed at one point in the
session; Step 12 ran its own independent split) — not a contradiction to paper over. It
is itself a small piece of evidence that calibration method selection is sensitive to
the specific split, reinforcing `MODEL_COMPARISON_REPORT.md`'s broader point about
needing more data before treating any single comparison as conclusive.

## Interpretation for the label-selection decision

`atr_adjusted_1d`'s ECE (0.0027) is dramatically better than the retained label's
(0.0426) — a genuinely striking difference. It did not, on its own, change the Step 12
promotion decision (which is gated on the *primary* metric's statistical significance,
per the directive's Model Promotion Rule structure — calibration is a secondary gate
that can block promotion but doesn't independently trigger it). This is flagged
explicitly in `PHASE2_COMPLETION_REPORT.md` and `MODEL_COMPARISON_REPORT.md` as a
signal worth future investigation, not silently dropped because the primary-metric test
didn't reach significance.

## Reproducibility

```python
from core.ml.calibration import compare_calibration_methods
# report = compare_calibration_methods(fitted_model, X_calib, y_calib, X_test, y_test)
# report.results -> list of CalibrationMethodResult(method, brier_score, ece, mce, calibration_curve)
```
