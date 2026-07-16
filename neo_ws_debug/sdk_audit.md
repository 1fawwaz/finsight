# SDK Behavior Audit (Step 9)

Installed package: `neo_api_client` (PyPI/dist-info name: `neo-api-client`), version
string in `venv/Lib/site-packages/neo_api_client-2.0.0.dist-info` reads **2.0.0**,
despite being installed from git tag **v2.0.2** (see `requirements.txt`'s pin:
`neo_api_client @ git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git@v2.0.2`).
This mismatch between the git tag and the package's own internal version metadata
is itself a minor SDK inconsistency, noted here as required by 9.7 — confirmed by
reading the dist-info folder name directly, not assumed.

All answers below are from reading the **installed** source under
`venv/Lib/site-packages/neo_api_client/`, not the published docs or the bundled
(and separately confirmed stale) `demo.py`.

---

## 9.1 — Does `subscribe()` send a different payload when `isIndex=True`? Show the transformation.

Yes, at the binary wire-protocol level (not the JSON-looking Python call).

- `NeoAPI.subscribe()` → `neo_api.py:627` → `self.NeoWebSocket.get_live_feed(instrument_tokens=instrument_tokens, isIndex=isIndex, isDepth=isDepth)` (`neo_api.py:655`).
- `NeoWebSocket.get_live_feed()` (`NeoWebSocket.py:332`) sets:
  ```python
  subscription_type = ReqTypeValues.get("SCRIP_SUBS")   # "mws"
  if isIndex:
      subscription_type = ReqTypeValues.get("INDEX_SUBS")  # "ifs"
  ```
  (`NeoWebSocket.py:339-341`)
- This `subscription_type` becomes the outer `"type"` field in the JSON-shaped
  request handed to `HSWebSocket.hs_send()` (`HSWebSocketLib.py:1188`), which
  branches on it (`HSWebSocketLib.py:1222` `SCRIP_SUBS` vs `HSWebSocketLib.py:1227`
  `INDEX_SUBS`) to call `prepareSubsUnSubsRequest(scrips, ..., SCRIP_PREFIX, ...)`
  vs `prepareSubsUnSubsRequest(scrips, ..., INDEX_PREFIX, ...)` — `SCRIP_PREFIX =
  "sf"`, `INDEX_PREFIX = "if"` (`HSWebSocketLib.py:1158-1159`).
- `prepareSubsUnSubsRequest` → `getScripByteArray(scrips, scrip_prefix)`
  (`HSWebSocketLib.py:511, 544`) prefixes **every individual scrip string** with
  `scrip_prefix + "|"` before encoding it into the binary buffer
  (`HSWebSocketLib.py:518`: `scripArray[index] = a + "|" + scripArray[index]`).

**Concrete transformation**: for the same `instrument_token`/`exchange_segment`
pair, an equity subscribe embeds `"sf|nse_cm|<token>"` into the binary payload;
an index subscribe (`isIndex=True`) embeds `"if|nse_cm|<token>"` instead. The
outer subscription-type byte in the binary frame header also differs (`ifs`'s
numeric encoding vs `mws`'s). This is a real, confirmed byte-level difference,
not a guess.

## 9.2 — Does the SDK internally rewrite `"Nifty 50"` (or other index names) before transmission?

No — and this question's premise doesn't apply the way it might seem to. `subscribe()`
never accepts a display name like `"Nifty 50"` at all; it only accepts
`instrument_token`/`exchange_segment` pairs (`NeoWebSocket.py:332`, `HSWebSocketLib.py
input_validation` at `HSWebSocketLib.py:230` requires exactly the keys
`["instrument_token", "exchange_segment"]`). Any name→token resolution happens
**before** `subscribe()` is ever called, via `scrip_master()`/`search_scrip()` — a
completely separate REST call, not part of the WebSocket subscribe path. So there
is no internal rewriting of index names inside `subscribe()` because no name ever
reaches it in the first place.

## 9.3 — What exchange segment values does the SDK use/require for equity vs. index?

Both use the **same** segment values — `settings.py:88-92`'s canonical
`exchange_segment` map only defines `nse_cm`/`bse_cm`/`nse_fo`/`bse_fo`/`cde_fo`/
`bcs-fo`/`mcx_fo` (plus case-insensitive aliases). There is no separate
`nse_idx`/`bse_idx` segment anywhere in the installed source. NIFTY and BANKNIFTY
both live in `nse_cm`; SENSEX lives in `bse_cm` — confirmed live against the real
Scrip Master in this investigation's prior runs (`pExchSeg` column: `nse_cm`/
`bse_cm` respectively). The equity/index distinction is carried entirely by the
`isIndex` flag → `subscription_type`/prefix (see 9.1), never by exchange segment.

## 9.4 — Does the SDK silently drop subscriptions it considers unsupported, rather than erroring?

Partially yes, in `NeoWebSocket.channel_segregation()` (`NeoWebSocket.py:536`):
tokens beyond 200 per channel across channels 2-16 are silently truncated — if
more than `200 * 15` tokens are ever queued, the excess is dropped with no
exception and no callback (the loop just runs out of channels and returns
whatever fit). Not relevant to this investigation's 1-4 token subscriptions, but
a real, confirmed silent-drop path for large subscription batches.

Separately, `HSWebSocketLib.py:503` (`is_scrip_ok`) rejects (returns `None` from
`prepareSubsUnSubsRequest`, `HSWebSocketLib.py:542`) if more than
`MAX_SCRIPS = 100` (`HSWebSocketLib.py:12`) scrips are requested in one call — again
silent (a `print()` only, no exception, no callback) for this specific limit.
Also not triggered by this investigation's small subscriptions, but a confirmed
silent-failure path in the same function family used for every subscribe call.

## 9.5 — Does the SDK receive acknowledgement frames that never get surfaced to user-facing callbacks?

Yes, confirmed. `NeoWebSocket.on_hsm_message()` (`NeoWebSocket.py:88`) inspects
`req_type` when the connection acknowledgement (`"cn"`) arrives and re-triggers
`subscribe_scripts()`/`call_quotes()` internally (`NeoWebSocket.py:100-103`) —
this "cn" ack is consumed entirely internally and **never** reaches the
user's `on_message`. Likewise the `"unsub"` ack (`NeoWebSocket.py:104-115`) only
reaches `on_message` as the literal string `"Un-Subscribed Successfully!"`, not
the original ack frame. So: yes, real server acknowledgement frames exist and
are consumed by the SDK without exposing their actual content to user code.

**Separately and more importantly for this investigation**: `StartServer.on_close`
(`HSWebSocketLib.py:1148-1151`) receives the real `close_status_code` and
`close_msg` from the underlying `websocket-client` library, but **discards both**,
calling only `self.onclose()` with zero arguments:
```python
def on_close(self, ws, close_status_code, close_msg):
    if(self.on_close):
        self.onclose()
```
This is why every close event this codebase has ever logged shows only the
generic, hardcoded string `"The Session has been Closed!"` (set in
`NeoAPI`'s own `__on_close` wrapper) rather than the real WebSocket close code —
the actual close code/reason never survives past this line. Confirmed by
reading the source, not inferred from behavior alone.

## 9.6 — Is there internal filtering that could drop index frames before `on_message` fires?

Yes — `NeoWebSocket.on_hsm_message()`, `NeoWebSocket.py:131`:
```python
if len(self.sub_list) >= 1 and self.is_message_for_subscription(message):
    if self.on_message:
        self.on_message({"type": "stock_feed", "data": message})
```
`is_message_for_subscription()` (`NeoWebSocket.py:141-153`) only forwards a
message to `on_message` if at least one item in the incoming list has a `"tk"`
key whose value matches a key already in `self.sub_list`. If a frame ever
arrived with a token not in `self.sub_list` (e.g. a stale subscription, or a
malformed key), it would be silently swallowed — never reaching `on_message`,
never logged anywhere in the SDK. This is a real, confirmed filter that exists
strictly between "raw frame parsed off the socket" and "on_message invoked" —
exactly the gap this investigation's raw-frame capture (`raw_frames.jsonl`,
captured below `on_hsm_message`, at the `websocket-client` `recv_data_frame`
level) is positioned to catch independently of this filter.

## 9.7 — Concrete divergences between the installed implementation and published docs

- The bundled `demo.py` (confirmed in an earlier session) shows a `login()`/
  `session_2fa()` flow and constructor kwargs that no longer exist in this
  installed version — already documented in `KOTAK_NEO_INTEGRATION_REPORT.md`.
- Package version metadata says `2.0.0` (dist-info folder name) while the
  installed git tag is `v2.0.2` — a labeling inconsistency, confirmed above.
- `prepare_connection_request()` (`HSWebSocketLib.py:461-475`, the older
  `USER_ID`/`SESSION_ID`-based connection path) references
  `BinRespTypes.get("END_OF_MSG")` at line 474, but `BinRespTypes`
  (`HSWebSocketLib.py:32-43`) has **no** `"END_OF_MSG"` key — `.get()` would
  silently return `None` and the subsequent `buffer[...] = None` assignment
  would raise `TypeError`. This function is **not** on the code path this
  investigation (or FinSight) uses (`Authorization`/`Sid`-based auth routes to
  `prepareConnectionRequest2` instead, `HSWebSocketLib.py:1211-1217`, which does
  not have this bug), but it is dead/broken code confirmed present in the
  installed package.

## 9.8 — Installed SDK version vs. latest published release; protocol-relevant changelog entries

Checked via `gh api repos/Kotak-Neo/Kotak-neo-api-v2/releases` (public GitHub
API, read-only, not a live trading action):

| Version | Published | Changelog |
|---|---|---|
| v2.0.2 (installed) | 2026-06-08 | "Fix holdings endpoint sending deprecated sId query param" |
| v2.0.1 | 2025-11-07 | "mcx now tradeable, MTF allowed as a product type, search scrip fix, readme changes" |
| v2.0.0 | 2025-10-23 | Major REST API stack migration (new consumer_key auth, removed QR login, endpoint restructuring) |

**v2.0.2 is the latest published release, and this is what's installed.** None
of the three changelogs mention the WebSocket/streaming protocol, subscription
message format, or index-feed handling at all — the only WS-relevant SDK files
(`NeoWebSocket.py`, `HSWebSocketLib.py`) show no version-gated branches or
deprecation notices for the streaming path. **No confirmed version/protocol gap
was found.** This directly informs the final classification: per this task's own
tie-breaker rule, the absence of a documented breaking change here means
classification **J** (server protocol incompatibility) cannot be selected on
this basis alone — only Step 10's close-code evidence could support J instead of
H.

## Conditional token-method test (per the task's own instructions)

The Scrip Master token path and the (unused, since `subscribe()` never accepts
names) name-based path are **not** materially different code paths — as shown
in 9.2, there is no separate "subscribe by name" implementation to compare
against; every subscribe call, equity or index, goes through the identical
`instrument_token`/`exchange_segment` → binary-frame path. Per the task's own
instruction ("only run [the token-method test] if this audit shows the Scrip
Master token path is a materially different code path... Otherwise skip and
state that source inspection already answers the question"), **this
confirmatory test is skipped** — source inspection already shows both paths are
one and the same.
