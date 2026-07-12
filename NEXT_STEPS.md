# NEXT_STEPS.md

**Status: no in-progress work.** Phase 1, Phase 2, and Phase 3 are all complete, tested,
and committed (see `SESSION_STATE.md`). This file is a menu of legitimate follow-ups, not
a queue of unfinished tasks.

---

## If resuming immediately, do this first

1. Read `SESSION_STATE.md` in full — it has commit hashes, dataset/model details, and the
   exact resume commands. Do not re-analyze the repo from scratch.
2. Run `python -m pytest -q` to reconfirm the 346-test baseline still passes before
   touching anything.
3. Ask the user which of the options below (or something new) they want next — none of
   these are pre-authorized to start without confirmation.

---

## Option A — Push to remote

Local `master` is ~19 commits ahead of `origin/master` (last known push: `9884539`;
current HEAD: `0835aee`). Lowest-effort option if the user just wants GitHub current.

```bash
git log origin/master..HEAD --oneline   # review what would be pushed
git push origin master
```

## Option B — Expand the ML training universe

The data/feature pipeline already supports more symbols with no code changes — only
longer ingestion/training time. Candidate: grow from 15 symbols toward Nifty 100.
Re-run `data_layer` → `feature_pipeline` → `training` → `generalization` →
`registry` for a new dataset version (e.g. `dataset_v2`); do not overwrite `dataset_v1`.

## Option C — Live UI verification of the ML Signals page

Phase 3's registry integration was verified via direct Python calls and Docker exec, but
not yet clicked through in the Streamlit UI itself. Launch the app
(`streamlit run app.py`) and confirm the ML Signals page surfaces the registered
XGBoost model's prediction (not the old RandomForest fallback) for at least one ticker.

## Option D — Revisit the improvement loop with a genuinely new idea

All 3 attempted improvements (ensemble, SHAP pruning, recency weighting) were reverted —
none beat the champion. Don't re-run the same 3 ideas. If pursued, needs an actually
different angle (e.g. a different feature set, a different target horizon, or expanding
the training universe first per Option B, then re-attempting).

## Option E — New feature work outside ML

Phases 1 and 2 are feature-complete per the v4.0 spec. Any new page/feature request
should follow the same standing rules that governed this whole build: no fabricated
data, India-only scope, preserve SQLite/SQLAlchemy, never touch `.env`, one feature at a
time with test → browser-verify → commit, explain-first for any architectural/DB change.

---

## Do NOT do without asking first

- Do not re-run the 3 already-failed improvement-loop ideas expecting a different result.
- Do not downgrade `xgboost` back below 3.3.0 to revert to Python 3.11 — this was a
  deliberate, explained tradeoff (see SESSION_STATE.md §16).
- Do not delete `data/backups/finsight_pre_phase3_20260712_235839.db` — it's the
  pre-Phase-3 restore point.
- Do not modify `.env` under any circumstances (hard rule, not situational).
