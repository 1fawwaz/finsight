# Kotak Neo SDK Transport Verification — Final Report

Standalone investigation (`neo_ws_debug/`), no FinSight code touched or imported.
One authentication, one logout, no retries, no reconnect anywhere in this run —
confirmed by `run_log.txt` and `callbacks.log` (exactly one `kotak_auth_success`,
exactly one `logout_result=succeeded`).

## Root cause, with evidence

**The transport, authentication, subscription protocol, and SDK plumbing are all
confirmed working — for equities.** The exact same connection, same session,
same wire protocol, same subscription mechanism produces zero market-data frames
for indices specifically, despite the server explicitly acknowledging the index
subscription request.

Evidence, cited by file/line/timestamp:

1. **Auth**: `run_log.txt:4-6` — `totp_login_result ok=True latency_s=1.20` at
   `11:15:59Z`, `totp_validate_result ok=True latency_s=1.09` at `11:16:00Z`,
   `kotak_auth_success` immediately after. Exactly one occurrence in the whole
   log (grep confirms).

2. **REST quotes work for equities, explicitly reject indices** —
   `quotes_results.json`:
   - `equities.RELIANCE-EQ.response` = `[{'exchange_token': '2885', 'display_symbol': 'RELIANCE-EQ', 'exchange': 'nse_cm', 'ltp': '1296.6000'}]` (also `TCS-EQ`→2201.00, `INFY-EQ`→1082.40 — all real, live LTPs).
   - `indices.NIFTY/BANKNIFTY/SENSEX.response` = `{'fault': {'code': '400', 'description': 'Please pass valid neosymbol values for getQuote', 'message': 'Invalid neosymbol values'}}` for **all three**, using the identical numeric tokens (`26000`/`26009`/`1`) that work fine for `subscribe()` later in the same run (see below). This is a distinct finding from the WebSocket silence: `quotes()`'s REST endpoint specifically rejects these index tokens with an explicit, server-stated 400 error — a different symptom, in a different code path, from the streaming issue. Layer: **REST**.

3. **REST-vs-WebSocket correlation (RELIANCE, Step 3)** — `correlation_report.json`:
   `rest_quote_before_ws.response` = `ltp: '1296.6000'` at `11:16:16.575Z`. The
   first WebSocket data frame for RELIANCE (below) carries `'ltp': '1296.60'` —
   an exact match, ~1.4 seconds later. **The REST market-data backend and the
   streaming backend are demonstrably the same underlying data, consistent with
   each other.** Layer: **REST ↔ WebSocket correlation**, confirmed consistent.

4. **WebSocket connects genuinely** — `connection_report.md:3-4`: opened at
   `11:16:17.169Z`, TLS 1.3, cipher `TLS_AES_256_GCM_SHA384`, remote address
   captured directly from the live socket (`ws.sock.sock.getpeername()`). Layer:
   **Transport**, confirmed healthy.

5. **Equity subscription → real data, in under a second** — `raw_frames.jsonl`
   lines 2-5 / `subscription_trace.json` step 6: subscribe sent at `11:16:17.343Z`
   (`sf|nse_cm|2885` visible in the raw outbound bytes — `subscription_trace.json:29`),
   ack received at `11:16:17.529Z` (`raw_frames.jsonl:4`, `\x04` = `SUBSCRIBE_TYPE`),
   **real tick data received at `11:16:17.954Z`** (`raw_frames.jsonl:5`, 160
   bytes, decoded by the SDK's own parser into
   `{'type': 'stock_feed', 'data': [{'ltp': '1296.60', 'h': '1309.40', ...}]}`
   — `callbacks.log`, `sdk_on_message` entry). **Total tick latency: ~611ms from
   subscribe to data.** Layer: **Subscription + callback + parser**, all
   confirmed working for equities.

6. **Index subscription → acknowledged, then silent for the full 120s window** —
   `raw_frames.jsonl` lines 6-11 / `subscription_trace.json` step 7: three
   subscribe requests sent at `11:17:16.619-620Z`, with the raw outbound bytes
   showing the exact index-prefixed scrip strings `if|nse_cm|26000`,
   `if|nse_cm|26009`, `if|bse_cm|1` (`subscription_trace.json:90,97,104` — byte
   values `105,102,124` = ASCII `"if|"`, confirmed by direct decode). **Three
   acknowledgement frames arrived within ~200ms** (`raw_frames.jsonl:9-11`, all
   three byte-identical to the equity ack: `\x00\x06\x04\x01\x01\x00\x01K`) —
   proving the server received and accepted all three subscription requests.
   **Zero further frames of any kind arrived in the remaining ~118 seconds** of
   the 120-second observation window (`run_log.txt`: `Step 7 result: sent_frames=3
   recv_frames=3` — all 3 received frames are the immediate acks, none are data).
   Layer: **Server-side data delivery, specifically for index subscriptions.**

7. **No protocol-level rejection or forced disconnect occurred** —
   `connection_report.md:5-6`: `Close code: None`, `Close reason: None` —
   captured directly from the real (un-discarded) `websocket-client` close
   callback (installed via the `WebSocketApp.__init__` patch — see
   `interception_method.json`). The connection stayed open for the entire
   120-second index observation window and was torn down only by this script's
   own voluntary `client.logout()` afterward (`run_log.txt`: `logout_result=succeeded`
   immediately follows `Step 7 result`). This directly rules out classification
   **J** per this task's own tie-breaker rule (no close-code-based protocol
   rejection was found), and rules out any "the connection dropped before data
   could arrive" explanation.

8. **SDK is on the latest published version, no relevant changelog gap** —
   `sdk_audit.md` §9.8: installed `v2.0.2` (2026-06-08) is the latest GitHub
   release; its changelog and the two prior releases' changelogs (`v2.0.1`,
   `v2.0.0`) mention nothing about the WebSocket/streaming protocol,
   subscription format, or index-feed handling. No version/protocol gap found —
   confirmed via `gh api repos/Kotak-Neo/Kotak-neo-api-v2/releases` (public,
   read-only).

9. **No silent SDK-side filtering explains this** — `sdk_audit.md` §9.6 documents
   a real filter in `NeoWebSocket.on_hsm_message()` that could drop a frame
   whose token isn't in the local subscription list, but this investigation's
   raw-frame capture (`raw_frames.jsonl`) sits **below** that filter, at the
   `websocket-client` receive level — and it recorded zero inbound data frames
   for the index tokens at all, meaning the frames never arrived at the socket
   in the first place. The SDK-level filter in §9.6 is therefore not the
   explanation; the absence is server-side, not client-side.

## Files and functions "changed"

None in FinSight (explicitly out of scope and untouched — confirmed no writes
outside `neo_ws_debug/` this session). All work is new, additive files under
`neo_ws_debug/`:
- `run_verification.py` — the full Steps 0-8/10/11 instrumented script.
- `sdk_audit.md` — Step 9 source-code audit (offline, no live calls).
- `minimal_repro.py` — Step 13 standalone reproduction (<100 executable lines).
- `environment.json`, `quotes_results.json`, `correlation_report.json`,
  `callbacks.log`, `raw_frames.jsonl`, `subscription_trace.json`,
  `connection_report.md`, `first_equity_frame.json`, `first_index_name_frame.json`,
  `interception_method.json`, `run_log.txt` — generated evidence from the one
  live run.

## Interception method used (Step 5)

Method **#1** (monkey-patch the underlying `websocket-client` handler ahead of
the SDK's own handler) — specifically `websocket.WebSocket.send` /
`websocket.WebSocket.recv_data_frame` (below `neo_api_client`'s own `HSWrapper`
binary parsing) plus `websocket.WebSocketApp.__init__` (to recover the real
`close_status_code`/`close_msg` that `neo_api_client`'s own `StartServer.on_close`
discards — see `sdk_audit.md` §9.5). Recorded verbatim in
`interception_method.json`.

## Classification

**B — Equity transport works. Index transport fails.**

Mechanism (per **H**, since Step 9.8 found no version/protocol gap and Step 10
found no protocol-level close signal, ruling out J per this task's own
tie-breaker rule): *the server accepted the index subscription request (no
error, no rejection — three acknowledgement frames received), but never
delivered market data for any of the three index tokens before the session was
voluntarily logged out; the connection itself remained healthy the entire time,
proven by the fact it was the same live socket that successfully streamed real
equity data seconds earlier.*

This is not classification **D** (explicit entitlement failure) because the
WebSocket layer never stated a permission/entitlement error — it was simply
silent for index data specifically. (Separately, and worth flagging: the REST
`quotes()` endpoint *does* return an explicit 400 "Invalid neosymbol values"
error for the same index tokens — a real, distinct, additional data point, but
it's the REST quote-lookup endpoint's own symbol-format validation, not the
streaming path this investigation is centered on, and it doesn't by itself
explain the WebSocket silence since `subscribe()` accepted the identical tokens
without complaint.)

## What would resolve this further (since H, not D, was reached)

The remaining gap that only Kotak (support or account dashboard) can close:
confirm whether this UCC/API key has live index-feed entitlement distinct from
equity streaming entitlement. `minimal_repro.py` is ready to hand to Kotak
support as-is — it reproduces the exact same evidence (auth succeeds, quote
succeeds for equities, subscribe is acknowledged, no data arrives) independent
of this codebase.
