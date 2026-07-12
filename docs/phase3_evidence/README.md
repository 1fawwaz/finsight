# Phase 3 execution evidence

Raw output from the actual training/evaluation runs executed during the Phase 3 ML
pipeline build, kept as supporting evidence for the Model Accuracy Report and Final
Acceptance Gate (see the session's final report). These are not polished docs -- they're
the real JSON dumps the pipeline itself produced.

- `phase3_training_summary.json` -- best hyperparameters and mean/std CV metrics per
  model family from the initial Optuna search (10 trials x 3 folds each).
- `phase3_gate_summary.json` -- the Step 2.3.1 generalization-gate result for each
  family's initial best config (train/val/test metrics, gap %, fold std %, pass/fail).
- `phase3_correction_log.json` -- every corrective-action attempt for the 3 families
  that initially failed the gate (random_forest, catboost, xgboost all eventually
  passed; lightgbm did not after 3 genuine attempts and was dropped).
- `phase3_final_model_summary.json` -- the final selected model (xgboost)'s
  hyperparameters, real walk-forward fold metrics, and held-out test metrics.

The registered model artifact itself lives in `data/ml_models/` (gitignored, since
`data/` holds runtime state) with full lineage in the `ml_model_registry` SQLite table.
Full evaluation artifacts (confusion matrix, learning curve, feature importance, SHAP --
JSON + PNG) are in `data/ml_evaluation/<model_version>/`.
