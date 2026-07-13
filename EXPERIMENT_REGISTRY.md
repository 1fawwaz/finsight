# Experiment Registry — Phase 2 Step 11

Live snapshot of `ml_training_runs` (the experiment log, extended in Step 11) as of
2026-07-13, queried via `core.ml.experiment_tracking.get_experiment_history`. This is a
point-in-time export for reporting purposes — the live table is the actual source of
truth and keeps growing; re-run the query below for current numbers.

## Summary

- **Total experiments logged:** 53
- **Logged via the new `log_experiment` (Step 11, full metadata incl. git commit):** 1
  (id 53, this session's own evidence run)
- **Logged via Phase 3's original `_log_training_run`** (Optuna trials, including this
  session's Step 9 nested-CV evidence and Phase 3's original training runs):
  52 — these correctly show `git_commit_hash=None` for rows logged before Step 11
  extended the table, since that field didn't exist for them to populate. **Not
  backfilled retroactively** — an old row correctly lacks data that didn't exist when
  it was written, consistent with this project's standing practice (see
  `finsight/PHASE1_IMPLEMENTATION_LOG.md`'s identical treatment of `dataset_v1`).

## Most Recent 5 Experiments (real data)

| id | model_family | dataset_version | feature_version | git_commit_hash |
|---|---|---|---|---|
| 53 | random_forest | dataset_v2 | features_v1 | `123fc7323e9fc9ee19e437896889d119f607d493` |
| 52 | random_forest | nested_cv_outer_fold_2 | nested_cv_inner_tuning | `None` |
| 51 | random_forest | nested_cv_outer_fold_2 | nested_cv_inner_tuning | `None` |
| 50 | random_forest | nested_cv_outer_fold_1 | nested_cv_inner_tuning | `None` |
| 49 | random_forest | nested_cv_outer_fold_1 | nested_cv_inner_tuning | `None` |

Rows 49–52 are the real Step 9 nested time-series CV evidence run (2 outer folds × 2
inner trials); row 53 is this session's Step 11 evidence run with full metadata
(training duration, prediction latency, calibration results, feature importance,
notes) — see `PHASE2_IMPLEMENTATION_LOG.md` Step 11 for the exact values logged.

## Immutability Guarantee

Every row above was created by exactly one `INSERT` and has never been updated.
Verified structurally, not just documented: `core.ml.experiment_tracking` has no
`update_experiment` function anywhere in its public surface (a dedicated test,
`test_no_update_experiment_function_exists`, introspects the module's own names to
confirm this rather than trusting a docstring).

## Related Registries (Phase 2, also live and queryable)

| Registry | Table | Live count (2026-07-13) |
|---|---|---|
| Feature lifecycle | `feature_registry` | 2 entries: `price_zscore_20` (deprecated), `bollinger_pct_b` (active) — see `FEATURE_IMPORTANCE_REPORT.md` |
| Feature importance history | `feature_importance_snapshots` | 68 rows (2 experiments × 34 features, from the Step 10 drift-detection evidence run) |
| Market breadth | `market_breadth_daily` | 1,239 real trading dates (Step 4 evidence, across 17 real tracked symbols) |

## Reproducibility

```python
from core.database import get_session
from core.ml.experiment_tracking import get_experiment_history

with get_session() as session:
    history = get_experiment_history(session)  # optionally filter by model_family=/dataset_version=
```
