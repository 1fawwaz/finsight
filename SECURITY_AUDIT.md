# Security Audit

Phase 5b of the Production Stabilization Directive. Every check below is
evidence-based (a command run against the live code, or a deliberately
triggered real error), not a code-reading assumption alone.

## Hardcoded secrets

`grep` across every `.py` file in `core/`, `pages/`, `app.py` for API-key/
password/secret literal patterns: **0 found.** `.env` (which holds the real
`GEMINI_API_KEY`) is confirmed untracked (`git ls-files .env` returns empty) and
listed in `.gitignore`. `.env.example` documents `GEMINI_API_KEY` as the
expected variable without a real value.

## SQL injection

Every database access in the app goes through SQLAlchemy's ORM/Core query
builder (parameterized queries by construction) -- confirmed by `grep` for raw
string-interpolated SQL (`execute(f"...")`, `.format(...)` feeding a query,
`%`-formatting feeding a query): **0 found**, with one exception reviewed
individually: `core/database.py:553`'s `text(f"ALTER TABLE {table_name} ADD
COLUMN {column_name} {column_ddl}")`. All three interpolated values originate
exclusively from the hardcoded, developer-authored `_ADDITIVE_COLUMN_MIGRATIONS`
list -- never from user or external input -- confirmed safe by reading every
value that ever reaches this call.

## XSS (Cross-Site Scripting)

Two `unsafe_allow_html=True` call sites exist in the whole app (both added this
session, in the UI Transformation directive):
- `core/design.py`'s `inject_design_system()` -- a static, hardcoded CSS string
  with zero variable interpolation. No injection surface.
- `core/ui_components.py`'s `render_empty_state()` -- interpolates `title`/
  `body`/`icon` into raw HTML. **Real finding, fixed this phase**: no current
  call site passes user-controlled text (verified: every one of the 7 call
  sites across `app.py`/`pages/*.py` passes a hardcoded string literal), so
  there was no *live* vulnerability -- but the function itself didn't defend
  against a future call site introducing one. Fixed by escaping all three
  parameters via `html.escape()` before interpolation. Verified live:
  `<script>alert('title')</script>` now renders as the literal, inert text
  `&lt;script&gt;alert(&#x27;title&#x27;)&lt;/script&gt;`, confirmed via a new
  test (`tests/test_ui_components.py::test_render_empty_state_escapes_html_special_characters`).

## Unsafe deserialization / code execution

`grep` for `eval(`, `exec(`, `pickle.loads(`, unsafe `yaml.load(` (without a safe
loader): **0 found** anywhere in `core/`/`pages/`/`app.py`.

## Secrets in logs

`grep` for any log statement referencing `api_key`/`password`/`secret` (case-
insensitive): **0 found.** Additionally, deliberately verified (not assumed)
that the Gemini SDK's own exception messages -- which every Gemini call site logs
via `logger.warning(..., exc)` on failure -- never echo the API key itself: a
real call was made with an intentionally invalid fake key
(`AIzaSyFAKEKEYFORSECURITYTESTINGONLY1234`) and the resulting exception message
was inspected directly. Result: the fake key string does **not** appear anywhere
in the exception text (`400 API key not valid...` -- a structured gRPC status
message, no key echoed). Confirms the existing logging pattern is safe from this
specific leak vector.

## CSV export (Portfolio/Watchlist download buttons)

Reviewed for CSV-injection risk (a malicious cell value like `=cmd|'/c calc'!A1`
executing if the exported file is later opened in a spreadsheet program).
**Low/theoretical risk, not fixed**: the only text fields in these exports
(`Symbol`, `Name`, `Sector`) originate from the bundled, trusted NSE equity list
or yfinance ticker metadata -- never from free-text user input -- so there is no
direct path for a user to inject a formula payload into their own export. Logged
as a low-priority, defense-in-depth item in the Technical Debt Register rather
than fixed, since the realistic attack surface is effectively zero (would
require Yahoo Finance's own metadata to contain a formula string).

## Configuration / transport security

`.streamlit/config.toml` was reviewed for security-relevant overrides: no CORS
or XSRF protection settings are overridden, meaning Streamlit's secure defaults
apply (`enableCORS`/`enableXsrfProtection` both default `True`, not disabled
here). `showErrorDetails = false` is set, correctly preventing raw Python
tracebacks (which could reveal file paths or internal logic) from reaching
end users -- confirmed as an intentional, already-existing setting, not
something this session needed to add.

## Authentication / authorization

**Not applicable.** This is a single-user, local-only application with no
login system, no multi-tenant data separation, and no network-exposed
deployment target in its current form (confirmed by repository inspection: no
`auth`/`session`/`login` module exists anywhere in `core/`). Not a gap for the
system as currently scoped; would need to be designed before any multi-user or
internet-facing deployment.

## Summary

| Check | Result |
|---|---|
| Hardcoded secrets | 0 found |
| SQL injection | 0 vulnerable sites; 1 dynamic-SQL site reviewed and confirmed safe |
| XSS | 1 defense-in-depth gap found and fixed (escaped, tested) |
| Unsafe eval/exec/deserialization | 0 found |
| Secrets in logs | 0 found; Gemini exception-message leak vector actively tested and ruled out |
| CSV injection | Theoretical only, no user-text input path; logged as low-priority debt |
| Transport/config security | Secure defaults confirmed, not overridden |
| Auth/authz | N/A — single-user local tool, no login system exists |
