# Deployment Readiness Report

Date: 2026-07-14. Covers directive §10.

## What "deployment" means for this application

FinSight is a single-process Streamlit application backed by a local SQLite file —
there is no separate compiled "build" artifact, container image, or bundler step in
this repository (confirmed: no `Dockerfile`, no `package.json`/frontend bundler
config at the app level — the one exception, the Autocomplete component's own
`core/components/stock_autocomplete/frontend/`, has its own independent
`npm run build` step, already exercised and verified in this session's earlier
Autocomplete work). "Deployment" here concretely means: install
`requirements.txt` into a Python environment, then run
`streamlit run app.py`.

## Production dependencies install successfully

```
$ ./venv/Scripts/python.exe -m pip check
No broken requirements found.
```

Cross-checked every pinned version in `requirements.txt` against the actually
installed environment (`pip freeze`) — zero mismatches (see
`REPOSITORY_HEALTH_REPORT.md` for the full comparison). The `venv/` used throughout
this entire session's work (including the final 648-test regression run) is the
same environment `requirements.txt` describes.

## Production build/package completes successfully

- Python side: nothing to "build" — pure interpreted app, already confirmed to
  `py_compile` cleanly across every touched file.
- The one sub-component with a real build step, the autocomplete frontend
  (`core/components/stock_autocomplete/frontend/`), was built via `npm run build`
  during this session's earlier work; its output (`frontend/build/`) is present on
  disk and was directly exercised in every browser verification since, including
  this audit's own RC smoke test (search worked correctly in the browser this
  session).

## Application starts using production configuration

Verified directly this session: `streamlit run app.py --server.headless true`
(the same headless, non-dev flag used throughout this whole project, not a
`--server.runOnSave` or other dev-convenience flag) started cleanly with no errors
(see `RC_VALIDATION_REPORT.md` for the full startup log). `.streamlit/config.toml`
has `showErrorDetails = false`, the production-appropriate setting (generic error
messages, not raw tracebacks, shown to end users).

## No development-only packages required at runtime

`requirements.txt` lists only packages the running application actually imports at
some code path (streamlit, plotly, yfinance, pandas, pyarrow, numpy, SQLAlchemy,
scikit-learn, google-generativeai, python-dotenv, rapidfuzz) plus the ML training
stack (catboost, xgboost, lightgbm, optuna, shap) which the ML Signals page's
on-demand training path genuinely needs at runtime (not dev-only — `pytest`/
`pytest-cov` are the only two entries that are test-only, and their presence in
`requirements.txt` rather than a separate dev-requirements file is a pre-existing,
minor packaging choice, not something introduced this session, and not a functional
problem since `pip check` still shows zero conflicts).

## Deployment documentation accuracy

`docs/README.md` and `finsight/SESSION_STATE.md` describe the repo layout and
current state; no separate "how to deploy to production" runbook exists in this
repository beyond "install requirements, run `streamlit run app.py`" (implicit in
every session-start instruction across this project's own `CLAUDE.md`). This is
consistent with the application's actual shape (a local single-user tool) — there is
no gap between documented steps and actual steps because the actual steps are this
simple and match what's already stated.

## Summary

| Item | Status |
|---|---|
| Production dependencies install successfully | ✅ Verified — `pip check` clean, versions match |
| Production build/package completes | ✅ Verified — no Python build step needed; frontend sub-component build output present and exercised |
| App starts using production configuration | ✅ Verified — clean headless startup, `showErrorDetails=false` |
| No dev-only packages required at runtime | ✅ Verified (pytest/pytest-cov are the only test-only entries, harmless) |
| Deployment documentation accurate | ✅ Verified — matches the simple actual deployment shape |

**Overall: Deployment readiness verification PASSES.**
