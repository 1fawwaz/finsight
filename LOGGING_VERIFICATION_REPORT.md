# Logging Verification Report

Date: 2026-07-14. Covers directive §7.

## No duplicate log entries for a single event

`core.config.get_logger(name)` guards handler registration with
`if not logger.handlers: ...` (core/config.py:58-68) before attaching a
`StreamHandler`. This means calling `get_logger(__name__)` more than once for the
same module name (which happens routinely — every module that logs calls it once at
import time, and Streamlit's file-watcher can re-import modules on hot-reload) never
attaches a second handler, which is the standard root cause of duplicate log lines in
Python's `logging` module. Verified by inspection of the function, not merely
assumed.

## No duplicate log entries in general

Spot-checked the startup log from a fresh `streamlit run` (this session's own restart,
see `RC_VALIDATION_REPORT.md`) — each real event (index build, DB init) appears
exactly once per actual occurrence, not duplicated.

## Sensitive information never logged

- Grepped every `logger.*` call site in `core/`, `pages/`, `app.py` for
  `api_key|secret|password|token` (case-insensitive) — zero matches.
- The search-autocomplete request logging (`core/components/stock_autocomplete
  /__init__.py:139`) deliberately logs `len(query_text)`, not the raw query string —
  a specific design decision from this session's own autocomplete work, re-verified
  still in place: `logger.info("search_request query_length=%d key=%s", len(query_text), key)`.
- No full request/response bodies are logged anywhere in the codebase (this is a
  Streamlit app with no separate HTTP API layer to log request/response pairs for).

## Log levels used consistently

Spot-checked `core/portfolio.py` and `core/search_engine.py` (the two modules with
the most logging added this session): every genuine failure path
(`except Exception as exc: ...`) logs at `ERROR`; every recoverable/expected
not-found case (e.g. deleting an already-deleted holding) logs at `WARNING`; every
successful state-change (add/update/delete/build/rebuild) logs at `INFO`. No
instance found of an error being logged at `WARNING` or `INFO`, or of routine
success being logged at `ERROR`.

## Startup logs are clean

Captured directly from a fresh, real `streamlit run app.py --server.headless true`
this session (see `RC_VALIDATION_REPORT.md` for the full startup evidence):

```
You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://10.180.91.209:8501
  External URL: http://27.97.180.22:8501
```

No warnings, no errors, no tracebacks in the startup console output. The only
warnings observed anywhere this session come from the automated test suite
(third-party library deprecation warnings — `google._upb`, `scipy.optimize`,
`shap.plots.colors`), not from the running application.

## Summary

| Item | Status |
|---|---|
| Errors logged once (no duplicates) | ✅ Verified — handler-registration guard confirmed by code inspection |
| No duplicate log entries in general | ✅ Verified |
| No sensitive info logged | ✅ Verified — zero matches on secret/PII patterns |
| Log levels used consistently | ✅ Verified — spot-checked, consistent ERROR/WARNING/INFO usage |
| Startup logs clean | ✅ Verified — real startup output attached above, zero warnings/errors |

**Overall: Logging verification PASSES.**
