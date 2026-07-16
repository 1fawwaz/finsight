# Broker Architecture — Upstox Primary / Kotak Neo Secondary

This document records the structural decisions behind FinSight's `BrokerAdapter` seam
(`core/broker_adapter.py`) and what was deliberately deferred. See
`FINAL_AI_VALIDATION_REPORT.md` and the other `*_REPORT.md` files in this repo for the
unrelated Explainable-AI platform work; this file covers only the live-broker migration.

## Context

The originally supplied migration directive specified a full enterprise real-time
trading-infrastructure buildout: Redis Pub/Sub broadcast, OpenTelemetry tracing, a
FastAPI/Next.js split, WebSocket-server fan-out to many clients, chaos testing at
10,000-symbol scale, and an autonomous `.env2`-gated credential-polling loop. None of
that infrastructure exists in FinSight (a single-process Streamlit app, confirmed via
`docs/GOVERNANCE.md` and the pre-existing Kotak Neo integration's own design notes:
"No FastAPI, no separate backend, no existing WebSocket server"), and this session had
already had one real incident — an accidental live Kotak Neo broker authentication
triggered by a stray browser action — making an unsupervised, autonomous
credential-handling loop a concrete risk, not a hypothetical one.

The user confirmed (via `AskUserQuestion`, mid-directive) two governing decisions:
1. Scale the directive down to FinSight's actual architecture — keep the real goal
   (Upstox primary, feature-flagged, Kotak Neo secondary) but build it as an adapter
   seam inside the existing Streamlit singleton pattern.
2. Credentials go in the real `.env` (added with the user's own explicit, in-chat
   authorization at the time — they supplied `USE_UPSTOX_PRIMARY=true`,
   `UPSTOX_ANALYTICS_TOKEN=<redacted>`, `USE_KOTAK_SECONDARY=true` directly), and no
   code opens a real live connection without asking first.

## Engineering Decision Register

### EDR-1: The `BrokerAdapter` seam

- **Problem**: FinSight needed a second live-data broker (Upstox) alongside the
  existing Kotak Neo integration, switchable without a code change, while never
  duplicating prediction/risk/UI logic per symbol shape.
- **Alternatives considered**:
  - (a) A single `if USE_UPSTOX_PRIMARY: ... else: ...` branch inline at every call
    site that currently imports `core.kotak_market_data` directly.
  - (b) A full microservice split (matching the original directive) with a message
    broker between ingestion and the UI.
  - (c) A common `BrokerAdapter` interface (`core/broker_adapter.py`), one thin
    implementation per broker, one router function.
- **Chosen solution & trade-offs**: (c). Every call site (`render_live_market_data_panel`,
  `render_live_index_cards`, their fragments) now calls
  `get_active_broker_adapter()` once and never imports a broker-specific module. The
  trade-off is one extra layer of indirection (`NormalizedTick`/`BrokerError`
  translation) versus (a)'s zero-abstraction inline branching — judged worthwhile
  since (a) would have meant duplicating every branch at 4+ call sites and any future
  third broker would multiply that.
- **Rejected options**: (a) rejected for duplication risk across call sites and pages;
  (b) rejected as wildly disproportionate to this app's actual scale and explicitly
  out of scope per the user's own scoping decision above.
- **Rollback strategy**: Revert is `git revert` on the commits touching
  `core/ui_components.py`'s two `render_live_*` functions — they're the only places
  that changed behavior; `core/kotak_market_data.py` itself was never modified, so
  reverting the adapter layer alone restores the pre-migration Kotak-only behavior
  exactly.
- **Evidence**: `tests/test_broker_adapter.py::TestRouterWithRealAdapterModules` — the
  router resolves to the real `KotakAdapter`/`UpstoxAdapter` classes (not mocks) based
  on the flag, construction-only, no live connection.

### EDR-2: Scope scaling — what the original directive asked for vs. what was built

| Directive asks for | What FinSight actually has | This migration builds |
|---|---|---|
| Redis Pub/Sub broadcast to many WebSocket clients | One Streamlit process, no client fleet | Nothing — the existing in-memory singleton cache + `st.fragment(run_every=2)` polling pattern (already proven by the Kotak integration) serves every session in the process for free |
| OpenTelemetry traces/metrics endpoint | Structured `logging` via `core.config.get_logger`, no OTel collector | Nothing new — every adapter's `status()` dict (now includes `sequence_counters`) is the observability surface, same shape as `core.ml.system_health.run_all_checks` |
| FastAPI `/health/live`, `/status/broker` HTTP endpoints | No arbitrary-route server | `adapter.status()` + the existing live-data UI panel |
| Chaos harness at 10,000-symbol synthetic scale | FinSight tracks ~20 tickers | Unit tests injecting the same fault classes (duplicate/gap/out-of-order ticks, disconnect, 401) at the scale that matters here |
| `.env2` + autonomous credential-polling gate | `.env` already holds `GEMINI_API_KEY`/`KOTAK_*` under a "never modify without explicit authorization" rule | `UPSTOX_ANALYTICS_TOKEN` in the same real `.env`, added only with the user's own explicit, in-chat instruction; no polling loop written |
| 60-second flag-flip drill against a distributed rollout | No distributed rollout exists | `get_active_broker_adapter()` re-reads `os.environ` fresh on every call (see `_read_flags`) — the flip itself is a single dict lookup, proven instant by `test_flipping_the_flag_switches_routing_on_the_next_call_no_restart` |
| Persistent tick storage / replay subsystem | Not requested by this migration's own scope (`§0`: "What you are NOT doing") | Not built |

## Real SDK findings (Upstox, `upstox-python-sdk==2.28.0`)

Confirmed by installing the real package and reading its source directly (the same
discipline already used for Kotak Neo's `neo_api_client`) — nothing below is guessed:

- **Auth**: a single pre-issued bearer access token (`Configuration.access_token`), not
  a login flow. No `UPSTOX_CLIENT_ID` is actually required at runtime for market-data
  streaming (only `UPSTOX_ANALYTICS_TOKEN`), despite the original directive's `.env2`
  skeleton naming both.
- **Message shape**: binary protobuf, but the SDK's own `MarketDataStreamerV3.handle_message`
  already decodes it to a plain dict before our callback ever sees it. Confirmed via
  the compiled `MarketDataFeedV3_pb2` descriptor: no packet sequence-number field
  exists anywhere in the schema — consistent with Kotak's raw payload, so
  `core.tick_sequence.TickSequenceGuard` defaults to timestamp-only ordering for both
  brokers.
- **A real timestamp Kotak's integration doesn't have**: Upstox's `ltpc.ltt` field is a
  genuine broker-reported tick timestamp. Kotak's existing `Tick.timestamp` (left
  untouched by this migration, per the "don't rewrite working code" rule) is actually
  an ingest timestamp, not an exchange timestamp — `update_from_raw` never parses
  Kotak's own equivalent field (`ltt`) into anything. This means `NormalizedTick.exchange_ts`
  is honestly `None` on the Kotak path and genuinely populated on the Upstox path — an
  intentional asymmetry, documented in both adapters' docstrings, not a bug.
- **Reconnect**: the SDK has its own built-in fixed-interval `auto_reconnect`
  (confirmed in `streamer.py`: `self.interval` never grows). This service explicitly
  disables it (`streamer.auto_reconnect(False)`) and drives its own exponential
  backoff monitor thread instead, matching `KotakMarketDataService`'s existing
  2s→60s-capped pattern, for consistent reconnect behavior across both adapters.
- **Instrument key resolution**: Upstox instrument keys are ISIN-based
  (`"NSE_EQ|INE002A01018"`), not trading-symbol-suffixed like FinSight's own
  `"RELIANCE.NS"`. Resolved via the real `InstrumentsApi.search_instrument` REST
  endpoint (an in-memory-cached lookup, `_InstrumentKeyRegistry`) — analogous to
  Kotak's Scrip Master CSV lookup, but per-query REST rather than a bulk download.

## Live verification (2026-07-16)

With the user's explicit, in-chat go-ahead, the Upstox path was run against real
servers with the real `UPSTOX_ANALYTICS_TOKEN` in `.env` (Home page, which
auto-starts the live connection when credentials are configured). Real evidence:

- **Auth + WebSocket connect succeeded**: `upstox_auth_configured` →
  `upstox_ws_open`, no retry/backoff needed.
- **A real bug was found and fixed from live evidence, not guessed**: the first
  attempt failed instrument resolution for all three indices with a genuine API
  error — `errorCode: UDAPI1171, "Invalid exchanges.", invalidValue: "NSE_INDEX"`.
  `search_instrument`'s `exchanges` filter rejects `"NSE_INDEX"`/`"BSE_INDEX"` even
  though those exact strings are valid *instrument_key prefixes* — two different
  constrained value sets in Upstox's real API, not interchangeable (this module's
  original assumption). Fixed in `_InstrumentKeyRegistry.resolve`: index searches
  now omit the `exchanges` filter entirely and match by name instead; the equity
  path (untested by this run, since Home only subscribes indices) keeps the filter.
- **After the fix, real live ticks flowed correctly** for all three indices (Nifty
  50, Sensex, Bank Nifty) — confirmed via `live_tick_to_ui_latency` log lines
  showing sane, monotonically-growing latency values (~3.3s → ~12.3s over ~9s of
  real wall-clock time, i.e. growing at the expected 1:1 rate for one static
  snapshot tick not yet refreshed) — this also indirectly confirms the **`ltt`
  epoch-milliseconds assumption is correct**: had the unit been seconds instead,
  dividing by 1000 would have produced a wildly wrong (decades-off) `exchange_ts`
  and a nonsensical latency value, not a small, sane, steadily-growing one.
- **`SearchInstrumentResponse.data`'s runtime shape**: the fix above worked and
  correctly matched real index instruments, confirming `_extract_search_results`
  handles the real response shape without crashing — though the exact branch taken
  (bare list vs. dict-wrapped) was not separately logged, so this is confirmed
  working, not confirmed *which* shape it actually is.
- **Full regression suite re-run after the fix: 920 passed, 2 skipped, 0 failed** —
  zero regressions from the live-verified change.
- Server shut down cleanly afterward; log confirmed zero `upstox_ws_close`/
  reconnect/traceback lines at any point during the session.

**Still not live-verified**: OHLC candle-interval semantics
(`marketOHLC.ohlc`'s multiple intervals) — Home's index cards only read `ltp`, not
OHLC/volume, so this specific assumption remains unexercised by this run. Flagged,
not hidden — will be confirmed the next time a symbol subscribed via
`render_live_market_data_panel` (which does render open/high/low/volume) is
live-verified.

## Technical Debt Register

| Item | Why deferred | Interface contract to honor if built | Trigger condition |
|---|---|---|---|
| Redis Pub/Sub broadcast layer | No multi-process/multi-client fleet exists to broadcast to | Would sit behind `BrokerAdapter.all_ticks()`/`get_tick()` unchanged — a Pub/Sub-backed adapter implementation, not a new interface | FinSight ever runs as more than one server process needing shared live state |
| OpenTelemetry traces/metrics | No collector exists; structured logging + `status()` already give inspectable state | Trace context would wrap the existing `ingest_ts`/`exchange_ts` dual-timestamp fields already on `NormalizedTick` | An actual OTel collector/dashboard is stood up for this project |
| Chaos harness at 10,000-symbol scale | FinSight tracks ~20 tickers; unit tests already cover every fault class at real scale | `TickSequenceGuard`'s bounded per-symbol state (already size-capped) is the scaling-relevant piece and needs no interface change | FinSight's tracked-symbol count grows by an order of magnitude |
| FastAPI/Next.js split | Streamlit has no arbitrary-route server; `adapter.status()` is the health surface today | A REST health endpoint would just serialize `adapter.status()`/`_sequence_guard.counters` as JSON — no new data model needed | A separate frontend/backend split happens for reasons unrelated to broker data |
| Persistent tick storage / replay subsystem | Explicitly out of scope per this migration's own mission statement (§0) | Append-only sink receiving post-guard (`ACCEPT`/`GAP`) `NormalizedTick`s, zero-copy columnar layout, 10x-100x playback read speed | A future backtesting/replay feature is explicitly requested |
| Kotak's own `Tick.timestamp` never captures a real exchange timestamp | `core/kotak_market_data.py` was deliberately left unmodified (repository rule: don't rewrite working code without a reason tied to the current phase) | Would parse the already-present `ltt` raw key (already mapped in `_TICK_KEY_MAPPING`) into a new `exchange_ts` field on `Tick`, mirroring Upstox's `Tick.exchange_ts` | Kotak's dual-timestamp accuracy becomes load-bearing for a real feature (e.g. cross-broker latency comparison) |

## Verification

- `pytest -q`: **920 passed, 2 skipped, 0 failed** (final count, this migration) —
  up from the pre-migration baseline of 827 passed / 1 skipped (see
  `FINAL_AI_VALIDATION_REPORT.md`); the second skip is the new
  `tests/test_upstox_live.py`, correctly inert by default (same opt-in gate as the
  pre-existing Kotak live test). Zero failures at any step of this migration.
- Sequence guard: 19 tests (`tests/test_tick_sequence.py`) covering duplicate/gap/
  out-of-order/bounded-memory in both sequence-id and timestamp-only modes.
- Adapter interface + router: 16 tests (`tests/test_broker_adapter.py`), including a
  no-restart flag-flip proof and real-class resolution (not mocks).
- Kotak adapter: 17 tests (`tests/test_kotak_adapter.py`), including the new
  `BrokerError` translation added during this migration.
- Upstox adapter + service: 41 tests (`tests/test_upstox_market_data.py` +
  `tests/test_upstox_adapter.py`) against the real, confirmed protobuf-decoded
  message shape and real model field names — zero live connection in any of them.
- `tests/test_upstox_live.py` (mirrors `tests/test_kotak_live.py`'s opt-in-only
  pattern exactly) exists as a scripted, repeatable live-verification path
  (`RUN_UPSTOX_LIVE_TEST=1 pytest tests/test_upstox_live.py -v -s`) but has not
  itself been run yet — live verification instead happened via the Home page's
  auto-starting live-index-cards panel (see "Live verification (2026-07-16)"
  above), with the user's explicit go-ahead. Both paths exercise the same
  `UpstoxMarketDataService`; running the scripted test is a reasonable next step to
  additionally confirm the equity-subscription path (Home only exercises indices).
- Every live broker connection opened during this migration (one Upstox session,
  auth+WebSocket+real ticks, see above) was opened only after the user's explicit,
  in-chat go-ahead for that specific action — consistent with this session's
  established protocol throughout.
