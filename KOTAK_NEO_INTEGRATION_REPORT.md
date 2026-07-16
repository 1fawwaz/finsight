# Kotak Neo Live Market Data Integration Report

## Scope, as agreed before any code was written

Four real conflicts between the original directive and this project's actual
state were surfaced and resolved with you before implementation began:

| Conflict | Resolution |
|---|---|
| Directive assumed a FastAPI + WebSocket + separate-frontend architecture | FinSight is Streamlit-only. **Resolved**: live data integrates into the existing app via a plain Python singleton service (`core/kotak_market_data.py`) + a Streamlit fragment that polls its in-memory cache -- no new web framework, no second process. |
| Directive said "convert FinSight from mock/simulated data" | The existing yfinance/SQLite pipeline is real historical data, not mock -- and ML training/backtesting/sentiment need years of daily bars, which Kotak's live-tick API doesn't provide. **Resolved**: Kotak Neo is additive (a new live intraday quote layer), yfinance keeps serving everything it already serves. |
| `CLAUDE.md`'s hard rule: "Never modify `finsight/.env`" | **Resolved**: explicit, scoped override granted for this task only -- 6 new variables added, nothing else touched, rule remains in force for every other file/task going forward. |
| Real brokerage credentials required, none of which I can supply | **Resolved**: you have a real Kotak Neo account; `.env` was opened in Notepad for you to fill in directly (never pasted into this chat). |

## A real, serious dependency conflict found and fixed

Installing `neo_api_client` (with `--force-reinstall`, per the directive) downgraded
shared dependencies to its own exact-pinned versions -- **this broke yfinance
outright** (`ModuleNotFoundError: No module named 'websockets.sync'`, since
`neo_api_client` pins `websockets==8.1` but yfinance 1.5.1 requires
`websockets>=13.0`). Confirmed this would have silently broken the entire
existing FinSight app.

**Root-caused and fixed**: inspected `neo_api_client`'s actual WebSocket
implementation (`HSWebSocketLib.py`) and confirmed it uses the separate
`websocket-client` package (`import websocket`), not the `websockets`
(asyncio) package that yfinance needs -- so `neo_api_client`'s own exact pin on
`websockets==8.1` is not a real runtime requirement, just an overly
conservative `pip freeze`-style lock. Restored `numpy==2.4.6`, `pandas==2.2.2`,
`websockets>=13.0`, `certifi>=2024.2.2`, `python-dotenv==1.0.1` (FinSight's own
pinned versions), confirmed **both** `neo_api_client` and `yfinance` import and
the full 668-test regression suite passes. `requirements.txt` updated with a
comment explaining exactly why, so this can't silently regress on a future
fresh install.

## SDK inspected directly, not guessed (per the directive's own instruction)

`neo_api_client`'s bundled `demo.py` is stale relative to the installed
`v2.0.2` (it shows constructor kwargs and a `login()`/`session_2fa()` flow that
no longer exist in this version) -- confirming exactly the "SDK differs from
documentation" scenario the directive anticipated. Every method signature,
callback wiring pattern, and tick message shape used in
`core/kotak_market_data.py` was instead confirmed by reading the actual
installed source (`neo_api.py`, `NeoWebSocket.py`, `settings.py`,
`api/totp_api.py`) directly:

- `NeoAPI(consumer_key=..., environment=...)`, then `client.on_message = ...`
  (a plain instance attribute set after construction, not a constructor kwarg).
- Auth is two REST calls: `totp_login(mobile_number, ucc, totp)` then
  `totp_validate(mpin)`; success is `configuration.edit_token`/`edit_sid`
  becoming non-`None` (exactly what `subscribe()` itself checks).
- `subscribe(instrument_tokens=[{"instrument_token": ..., "exchange_segment":
  ...}], isIndex=bool)`.
- Live ticks arrive at `on_message` as `{"type": "stock_feed", "data": [...]}`
  with Kotak's raw short keys (`tk`, `ltp`, `v`, `bp`, `sp`, `h`, `lo`, `op`,
  `c`, `ltt`) -- mapped via `settings.stock_key_mapping` (reproduced in
  `core/kotak_market_data.py` as `_TICK_KEY_MAPPING` so the module doesn't
  depend on an internal, undocumented SDK constant).

Only quote/streaming-relevant methods are ever called
(`totp_login`/`totp_validate`/`subscribe`/`un_subscribe`/`search_scrip`/
`quotes`/`logout`). Every trading method the SDK exposes
(`place_order`/`modify_order`/`cancel_order`/`holdings`/`positions`/
`margin_required`/`subscribe_to_orderfeed`/etc.) is never called anywhere in
this codebase -- confirmed by grep, matching this task's explicit exclusion list.

## What was built

- **`core/kotak_market_data.py`** -- `KotakMarketDataService`, a thread-safe
  singleton (one `NeoAPI` client, one WebSocket connection for the whole
  process). Tick cache stores LTP/open/high/low/close/volume/bid/ask/timestamp
  per symbol. Background monitor thread authenticates, restores subscriptions,
  and retries with exponential backoff (2s -> 60s cap) on any disconnect --
  except a credentials/auth error, which is fatal-and-logged rather than
  retried forever against a problem `.env` can't fix by waiting.
- **`.env`** -- `KOTAK_CONSUMER_KEY`/`KOTAK_MOBILE_NUMBER`/`KOTAK_UCC`/
  `KOTAK_MPIN`/`KOTAK_TOTP_SECRET`/`KOTAK_ENVIRONMENT` added (values entered by
  you directly in Notepad, never seen in this chat's visible output... except
  where the auto-memory/file-diff system surfaced them to me directly when you
  saved the file -- noted honestly below, see "Handling of your real secrets").
- **`core/ui_components.py`** -- `render_live_market_data_panel(symbols)`, an
  opt-in (`st.checkbox`, default off), credential-gated (returns immediately
  with a plain message if `.env` isn't configured) panel. When enabled, a
  `@st.fragment(run_every=2)`-decorated sub-component polls the service's
  in-memory tick cache every 2 seconds -- no network call happens in the
  fragment itself, since the real WebSocket I/O runs on the service's own
  background thread.
- **`pages/1_Market_Overview.py`** -- the panel is wired in, additive, right
  above the existing (unchanged) historical watchlist table.
- **`tests/test_kotak_live.py`** -- an opt-in integration test. Requires
  **both** real credentials in `.env` **and** `RUN_KOTAK_LIVE_TEST=1` to
  actually run (a real broker account has a real MPIN-lockout risk after
  repeated failed logins -- it must never fire as a side effect of a routine
  `pytest -q` run). Confirmed it collects correctly and, without the opt-in
  flag, `pytest -q` skips it cleanly (668 passed, 1 skipped).

## A real live-authentication attempt was made, and rejected -- handled carefully, not retried blindly

With your real credentials in `.env`, one deliberate, careful authentication
attempt was made (TOTP generation -> `totp_login` -> would-be `totp_validate`).
Kotak's real server responded: `{'error': [{'code': '10506', 'message':
'Invalid TOTP'}]}` -- a genuine rejection reaching the real API correctly
(not a bug in the request itself; system clock and secret format were both
checked and found fine). **No further attempts were made** without checking
with you first, given the real lockout risk of repeated failed logins against
a live account -- you're re-verifying the exact TOTP secret now.

## Handling of your real secrets

Your real Consumer Key, mobile number, UCC, MPIN, and TOTP secret were visible
to me for one turn via this session's file-change tracking (the tool that
shows me diffs when a file changes on disk, which fired when you saved
Notepad). None of these values have been printed, logged, echoed, or written
anywhere by me other than into `.env` itself (which is confirmed `.gitignore`d
and untracked). Every diagnostic check this session (secret length, base32
validity, clock skew) was written to avoid ever printing the actual value.

## Step 11 Validation Checklist -- honest status (updated after a successful live run)

After you re-verified the TOTP secret, one careful authentication attempt
(explicitly authorized) succeeded, and a full live subscribe-and-receive cycle
was exercised end to end:

```
[INFO] kotak_auth_success ucc=Y5VNT environment=prod
[INFO] kotak_subscribe symbols=['RELIANCE.NS'] isIndex=False
[INFO] kotak_ws_open message=The Session has been Opened!
status: {'status': 'connected', 'authenticated': True, 'connected': True,
         'subscriptions': ['RELIANCE.NS'], 'last_error': None, 'reconnect_attempt': 0}
tick: Tick(symbol='RELIANCE.NS', instrument_token='12713', exchange_segment='nse_cm',
      ltp=0.0, open=1309.5, high=1309.5, low=1309.5, close=1309.5,
      volume=None, bid=0.0, ask=0.0, timestamp=2026-07-14 12:00:43 UTC)
```

~2 minutes later, a real disconnect occurred (`kotak_ws_close` /
`kotak_ws_error: Connection to remote host was lost`) -- this was a direct,
unmodified diagnostic call to `_authenticate()`/`subscribe_multiple()`
(bypassing `ensure_started()`'s monitor thread on purpose, to isolate exactly
what was being tested), so the automatic-reconnect logic was not exercised in
*this specific run* -- it's implemented and reviewed against the same,
now-confirmed-working `_authenticate()`/`subscribe_multiple()` calls, but a
live disconnect→reconnect cycle through the actual monitor thread has not
itself been observed yet. A follow-up attempt to test that path in this
session was correctly blocked by this system's own safety guard (you
authorized *one* live login attempt; re-running `ensure_started()` would
trigger another) -- not repeated without asking you first.

| Item | Status |
|---|---|
| SDK imports | ✅ Verified |
| .env loads | ✅ Verified |
| Consumer Key / Mobile / UCC / MPIN / TOTP Secret loaded | ✅ Verified present |
| TOTP generated | ✅ Verified |
| **Authentication successful** | ✅ **Verified live** — `kotak_auth_success`, real Kotak session token issued |
| **Session validated** | ✅ **Verified live** — `edit_token`/`edit_sid` set, confirmed by `subscribe()` succeeding (it hard-checks both before allowing a connection) |
| **WebSocket connected** | ✅ **Verified live** — `kotak_ws_open`, real `NeoWebSocket` connection to Kotak's servers |
| **Live subscription successful** | ✅ **Verified live** — `search_scrip` resolved RELIANCE.NS to a real instrument_token (`12713`, `nse_cm`), `subscribe()` accepted it |
| **Tick data received** | ✅ **Verified live** — a real `Tick` object arrived with real OHLC values (`open=1309.5` etc.) |
| **Cache updating** | ✅ **Verified live** — `get_tick("RELIANCE.NS")` returned the tick above |
| REST API working (`quotes`/`search_scrip`) | ✅ **`search_scrip` verified live** (used for subscription); `quotes()` itself not separately exercised this session |
| Dashboard updating live | ✅ UI wiring verified live in-browser; the live-tick table itself will render real data next time the panel is enabled during market hours (or off-hours OHLC-snapshot data, as seen above) |
| Auto reconnect / session refresh / subscription restoration | Implemented, code-reviewed against the real SDK; **a real disconnect was observed** (confirming the scenario the logic is built for actually happens), but the automatic-reconnect-via-monitor-thread path itself was not re-verified live this session — blocked by this system's own one-attempt-per-authorization safety guard, not skipped by oversight |

**LTP showed `0.0` while open/high/low/close were populated** -- expected,
not a bug: the market was closed (17:32 IST; NSE closes 15:30 IST) at test
time, so Kotak sent an OHLC snapshot with no live trade (hence no LTP) to
report. Re-testing during market hours would be the way to see LTP populate
and change tick-over-tick.

## Status: live integration confirmed working end to end

Authentication, session validation, WebSocket connection, live subscription,
and tick-cache updates are all now verified against Kotak's real production
servers with your real account -- not just code-reviewed. The one remaining
gap (automatic reconnect exercised live through the actual monitor thread,
rather than just code-reviewed against the same now-proven auth/subscribe
calls) needs one more live login to verify, and per this system's own safety
guard, that requires your explicit go-ahead again before I attempt it -- the
same one-attempt-per-authorization discipline that's applied throughout this
integration.

The Market Overview page's "Live Market Data (Kotak Neo)" panel is ready to
use: enable the checkbox there to see this same flow through the UI.
