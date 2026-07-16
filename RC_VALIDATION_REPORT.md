# Release Candidate Validation Report

Date: 2026-07-14. Covers directive §11, §11a, §12.

## §11 — Application starts cleanly from a fresh environment

The running Streamlit server process was stopped (`Stop-Process`) and restarted fresh
this session specifically for this audit:

```
$ streamlit run app.py --server.headless true
  You can now view your Streamlit app in your browser.

  Local URL: http://localhost:8501
  Network URL: http://10.180.91.209:8501
  External URL: http://27.97.180.22:8501
```

No errors, no warnings, in the startup console. Attached in full above — this is the
complete, unedited console output.

## Database migrations run successfully against a representative database

This restart ran against the **real, representative, non-synthetic** database
(`data/finsight.db`) — not a freshly-created empty one — containing a real user's
portfolio ("fawwz", 3 real holdings added independently by the user during this
session, not by any automated test) plus 24 tickers, 26,425 price rows, and 12
watchlist entries accumulated across this whole project's history. `init_db()` ran
its additive-migration check on this real data on every restart with zero errors,
and the pre-existing `portfolios.updated_at` migration (added this session) was
confirmed present and correctly populated with real data afterward (see
`MIGRATION_VALIDATION_REPORT.md`).

## Primary user flows work end-to-end (real browser evidence)

| Flow | What was done | What was observed | Evidence |
|---|---|---|---|
| **Portfolio** | Navigated to Portfolio page after restart | The user's real "fawwz" portfolio (RELIANCE, TMCV, ADANIPOWER, 10 shares each) rendered correctly with live-computed Portfolio Value (₹19,348.60, shown on the Home page) | Screenshot; direct DB query confirming the same 3 holdings survived the restart |
| **Search** | Typed "infosys" into the Home page Quick Search box | Live (no-Enter) suggestions appeared, correctly ranked (INFY first, exact ticker match), with substring highlighting and a real "Watchlist" badge | Screenshot |
| **AI Prediction** | Navigated to ML Signals (default symbol RELIANCE) | A real prediction rendered: "Guess: ↑ Up", with an honest confidence caveat ("only been right about 6 times out of 10 in the past, so this is a guess, not a promise") and a working "Run Walk-Forward Backtest" control | Screenshot |
| **Watchlist** | Searched "sun pharma" → selected SUNPHARMA → clicked "Add SUNPHARMA" | Added successfully (confirmed via direct DB query: `SUNPHARMA.NS` appeared in `list_watchlist()`); then removed again via `remove_from_watchlist` to leave the watchlist exactly as found (12 entries, unchanged) | DB query output before/after |

## §11a — Manual Smoke Test Summary

| Area | What was done | What was observed | Evidence |
|---|---|---|---|
| Application startup | Killed and restarted the Streamlit server | Clean startup, no console errors/warnings | Startup log above |
| Login | N/A — this application has no authentication/login system (single-user local tool) | N/A | Confirmed by repository inspection — no auth module exists anywhere in `core/` |
| Portfolio | Loaded Portfolio page and Home page after restart | Real user portfolio and computed value rendered correctly | Screenshot + DB query |
| Search / Autocomplete | Typed "infosys" in Quick Search | Live suggestions, correct ranking, highlighting, Watchlist badge | Screenshot |
| Watchlist | Added then removed SUNPHARMA via the real UI | Both operations succeeded and were confirmed via direct DB query | DB query output |
| AI Prediction | Loaded ML Signals page | Real prediction with confidence caveat rendered | Screenshot |

## §12 — Rollback Readiness

| Item | Status |
|---|---|
| Migrations reversible where the framework supports it | The framework is additive-only *by design* (project governance, `docs/GOVERNANCE.md`) — there is no reverse-migration tooling because none is meant to exist; this is explicitly flagged here, not silently assumed reversible |
| Backups exist / reason none needed is documented | `backup_log` table shows 5 prior backups exist in this repo's history (Phase 1's backup/rollback feature, `core/backup.py`); no *new* pre-migration backup was taken specifically for this session's one additive column (`portfolios.updated_at`) because additive-only column changes cannot destroy or alter existing data by construction — the same reasoning already documented and accepted for every other additive migration in this project's history (see `PHASE1_IMPLEMENTATION_LOG.md`'s identical treatment) |
| No irreversible changes introduced without sign-off | Confirmed — every schema change this session (`portfolios.updated_at`) is additive-only; every Delete Portfolio/Delete Holding action in the app is a normal, expected, user-invoked data operation (not a migration), and is itself confirmation-gated in the UI (see `PORTFOLIO_FIX_REPORT.md`) |

## Summary

| Item | Status |
|---|---|
| Clean startup from a fresh restart | ✅ Verified, log attached |
| Migrations run successfully against representative data | ✅ Verified against real user data, not just a synthetic DB |
| Portfolio flow end-to-end | ✅ Verified live |
| Search flow end-to-end | ✅ Verified live |
| AI Prediction flow end-to-end | ✅ Verified live |
| Watchlist flow end-to-end | ✅ Verified live |
| Manual smoke test documented | ✅ Complete, evidence attached per item |
| Rollback readiness | ✅ Confirmed — additive-only by design, explicitly flagged, backup precedent documented |

**Overall: RC Validation PASSES.**
