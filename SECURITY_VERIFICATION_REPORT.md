# Security Verification Report

Date: 2026-07-14. Covers directive §4.

## Secrets

| Check | Result | Evidence |
|---|---|---|
| `.env` tracked by git? | No | `git ls-files \| grep -i "\.env$"` → empty |
| `.env` ever committed in history? | No | `git log --all --full-history -- .env` → empty |
| `.env` listed in `.gitignore`? | Yes | `.gitignore` contains `.env`, `.env.backup`, `.streamlit/secrets.toml` |
| `.env.example` exists (documents required vars without values)? | Yes | `GEMINI_API_KEY=` (empty placeholder) |
| Hardcoded secret-looking string literals in tracked `.py` files? | None found | `git grep -niE "api_key\s*=\s*['\"][a-z0-9]\|secret\s*=\s*['\"]\|password\s*=\s*['\"]"` on tracked files, excluding legitimate `os.environ`/`os.getenv`/`config.` references → 0 matches |
| Secrets logged anywhere? | None found | `grep` for `logger.*` calls containing `api_key\|secret\|password\|token` → 0 matches |
| DB file (`finsight.db`) tracked by git? | No | `/data/` is gitignored; `git ls-files \| grep "\.db$"` → empty |

**Finding:** none. Both environment variables the app actually reads
(`DATABASE_URL`, `GEMINI_API_KEY` — confirmed via `grep -n "os.getenv" core/config.py`)
are sourced from environment/`.env`, never hardcoded. `DATABASE_URL` is missing from
`.env.example` (logged as AUDIT-002, Low severity, non-blocking since it has a safe
default).

## Debug endpoints / dev-only configuration

| Check | Result | Evidence |
|---|---|---|
| `debug=True` / verbose flags in app code (`core/`, `pages/`, `app.py`) | None | All `debug=True` hits are inside `venv/site-packages/` (numba, altair, pip, tornado, yfinance internals) — zero in application code |
| SQL echo (verbose query logging) enabled? | No | `core/database.py:513` — `create_engine(DATABASE_URL, echo=False, ...)` |
| Raw tracebacks shown to users on exceptions? | No | `.streamlit/config.toml` → `[client] showErrorDetails = false` — a generic message is shown instead of a Python traceback for any exception not already caught by a page's own try/except |
| Test-only routes/pages reachable in the running app? | No | `pages/` contains only the 7 real feature pages; no `pages/_test*.py` or similar exists |
| CORS / permissive network config | N/A for this app shape | Streamlit's own default CORS protection is unmodified — no `--server.enableCORS false` or similar flag is used in the run command (`streamlit run app.py --server.headless true`) |

**Finding:** none. No development-only configuration is active in the path the app
actually runs (confirmed by inspecting the exact command used to start it this
session, and the checked-in `.streamlit/config.toml`).

## File permissions / configuration exposure

This is a local, single-user Streamlit application backed by a local SQLite file —
there is no multi-tenant file-serving surface or web server exposing the filesystem
directly. Checked:

| Check | Result |
|---|---|
| `data/` directory world-writable or exposed via any route? | Not applicable — Streamlit doesn't serve arbitrary filesystem paths; `/data/` is gitignored and only accessed via `core.database`'s SQLAlchemy engine, never via a URL |
| Any file-upload path that could write outside `data/`? | `pages/3_Portfolio.py`'s CSV importer reads an uploaded file directly into `pandas.read_csv` in memory — never writes the uploaded file to disk, so there's no path-traversal write surface |
| `.streamlit/secrets.toml` present or referenced without being gitignored? | Not present on disk; correctly listed in `.gitignore` in case it's added later |

**Finding:** none.

## Summary

| Item | Status |
|---|---|
| No secrets committed (including history) | ✅ Verified |
| No debug endpoints in production path | ✅ Verified |
| No dev-only config active | ✅ Verified |
| File permissions / config exposure appropriate | ✅ Verified (N/A file-serving surface, confirmed no upload-write path) |

**Overall: Security verification PASSES**, with one Low-severity, non-blocking
documentation gap (AUDIT-002) logged in `AUDIT_TRAIL.md`.
