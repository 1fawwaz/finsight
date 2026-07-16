"""Live market data via the official Upstox Python SDK (`upstox-python-sdk` on PyPI,
import name `upstox_client`, v2.28.0) -- market-data-feed WebSocket ONLY
(quotes/streaming). Explicit scope boundary, same mandate as Kotak Neo's integration
(`core/kotak_market_data.py`): this module never places, modifies, or cancels an
order, and never touches holdings, positions, margin, or the order feed.

Every class/method/message shape used below was confirmed by reading the actual
installed `upstox_client==2.28.0` source directly (`upstox_client/feeder/
market_data_feeder_v3.py`, `market_data_streamer_v3.py`, `streamer.py`,
`configuration.py`, `api/instruments_api.py`, `models/instrument_data.py`, and the
compiled `feeder/proto/MarketDataFeedV3_pb2.py` descriptor) plus the installed
package's own bundled README usage examples -- nothing here is guessed. In particular:

- Auth is a single bearer access token, not a login flow: `Configuration(sandbox=False)`
  then `configuration.access_token = <token>` -- `ApiClient(configuration)` sends
  `Authorization: Bearer <token>` on the WebSocket's own connect headers
  (confirmed in `market_data_feeder_v3.py::connect`). No totp/mpin-style exchange
  exists for market-data streaming with a pre-issued analytics token.
- `MarketDataStreamerV3(api_client, instrumentKeys, mode)` -- modes are "ltpc"/"full"/
  "option_greeks"/"full_d30" (confirmed in `MarketDataStreamerV3.Mode`); this service
  uses "full" (LTPC + OHLC + depth + volume, per the SDK's own README), same
  information richness as Kotak's integration.
- Events via `.on(event, callback)`: "open"/"close"/"message"/"error"/"reconnecting"/
  "autoReconnectStopped" (confirmed in `Streamer.Event`). `on_message` callbacks
  already receive a decoded Python dict -- the SDK's own `MarketDataStreamerV3.
  handle_message` protobuf-decodes before emitting -- this service never touches raw
  protobuf bytes.
- The SDK's own `auto_reconnect(enable, interval, retry_count)` is a FIXED-interval
  retry (confirmed in `Streamer.__init__`/`launch_auto_reconnect`: `self.interval`
  never grows), not exponential backoff. This service explicitly disables it and
  drives its own exponential-backoff-with-jitter reconnect monitor thread instead --
  the same reconnect discipline already used in `core/kotak_market_data.py`.
- Message shape (mode="full", confirmed via the compiled protobuf descriptor):
  `{"type": "live_feed"|"initial_feed"|"market_info", "feeds": {instrument_key:
  {"fullFeed": {"marketFF": {"ltpc": {"ltp":..., "ltt":..., "ltq":..., "cp":...},
  "marketOHLC": {"ohlc": [...]}, "marketLevel": {"bidAskQuote": [...]}, "atp":...,
  "vtt":..., "oi":..., "tbq":..., "tsq":...}}}}, "currentTs": ...}`. No packet
  sequence-number field exists anywhere in this schema (confirmed against every
  message type's field list via the descriptor) -- consistent with Kotak's raw
  payload, and why `core.tick_sequence.TickSequenceGuard` defaults to timestamp-only
  ordering for both brokers.
- `ltt` (last traded time) is an int64 field, treated here as epoch milliseconds --
  a documented assumption (typical for this API family), **not yet confirmed against
  a real live tick**; flagged in `BROKER_ARCHITECTURE.md` for verification the first
  time this service actually runs against live data.
- Instrument keys are NOT trading-symbol-suffixed like FinSight's own "RELIANCE.NS" --
  they're "<EXCHANGE_SEGMENT>|<ISIN>" for equities (e.g. "NSE_EQ|INE002A01018") or
  "<EXCHANGE_SEGMENT>|<index name>" for indices (e.g. "NSE_INDEX|Nifty 50"), confirmed
  via the SDK's own bundled README examples. Resolved via the real
  `InstrumentsApi.search_instrument(query, exchanges=...)` REST endpoint (confirmed
  via `instruments_api.py` and `models/instrument_data.py`'s real field names:
  `instrument_key`, `trading_symbol`, `exchange`, `isin`, `name`) -- never a
  hardcoded/guessed instrument key. `SearchInstrumentResponse.data` is untyped
  (`swagger_types: {"data": "object"}`) in the installed SDK's own model, so its exact
  runtime shape (a bare list vs. a dict wrapping a list) is handled defensively here
  and flagged for confirmation against one real live search call.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from core.config import UPSTOX_ANALYTICS_TOKEN, get_logger
from core.tick_sequence import TickOutcome, TickSequenceGuard

logger = get_logger(__name__)

MAX_BACKOFF_SECONDS = 60
INITIAL_BACKOFF_SECONDS = 2

_EXCHANGE_SEGMENT_BY_SUFFIX = {".NS": "NSE_EQ", ".BO": "BSE_EQ"}

# Well-known index display names this integration supports, matching Kotak's own
# INDEX_SYMBOLS scope. Resolved dynamically via the real search_instrument API, never
# hardcoded to a guessed instrument_key.
INDEX_SYMBOLS: dict[str, str] = {
    "NIFTY 50": "NSE_INDEX",
    "NIFTY BANK": "NSE_INDEX",
    "SENSEX": "BSE_INDEX",
}
_INDEX_SEARCH_ALIASES: dict[str, str] = {
    "NIFTY 50": "Nifty 50",
    "NIFTY BANK": "Nifty Bank",
    "SENSEX": "SENSEX",
}

STATUS_STOPPED = "STOPPED"
STATUS_AUTHENTICATING = "AUTHENTICATING"
STATUS_AUTHENTICATED = "AUTHENTICATED"
STATUS_CONNECTED = "CONNECTED"
STATUS_SUBSCRIBED = "SUBSCRIBED"
STATUS_FAILED = "FAILED"
STATUS_RECONNECTING = "RECONNECTING"


def _safe_str(exc: BaseException) -> str:
    try:
        text = str(exc)
    except Exception:
        text = None
    if not isinstance(text, str) or not text:
        return f"{type(exc).__name__} (no further detail available)"
    return text


class UpstoxCredentialsError(Exception):
    """Raised when the required Upstox credential is missing from the environment."""


class UpstoxAuthError(Exception):
    """Raised when the configured access token is rejected (401) by Upstox."""


class InstrumentResolutionError(Exception):
    """Raised when a symbol cannot be resolved to an Upstox instrument_key via
    InstrumentsApi.search_instrument -- never guessed."""


def _to_upstox_symbol(symbol: str) -> tuple[str, str]:
    """FinSight's canonical "RELIANCE.NS"/"SBIN.BO" -> (bare_symbol, exchange_segment)."""
    symbol = symbol.strip().upper()
    for suffix, segment in _EXCHANGE_SEGMENT_BY_SUFFIX.items():
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)], segment
    if symbol in INDEX_SYMBOLS:
        return symbol, INDEX_SYMBOLS[symbol]
    raise ValueError(f"{symbol!r} is not a recognized NSE/BSE symbol or supported index")


def _extract_exchange_ts(feed: dict, instrument_key: str) -> Optional[datetime]:
    """Pulls the real broker-reported timestamp (`ltpc.ltt`, epoch milliseconds --
    see this module's docstring) out of one `feeds[instrument_key]` entry, without
    mutating anything -- shared by `Tick.update_from_feed` (applies it) and
    `UpstoxMarketDataService._on_message` (peeks it to run the sequence guard
    *before* deciding whether to apply the tick to the cache)."""
    full_feed = feed.get("fullFeed") or {}
    market_ff = full_feed.get("marketFF") or full_feed.get("indexFF") or {}
    ltt_raw = (market_ff.get("ltpc") or {}).get("ltt")
    if ltt_raw in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(ltt_raw) / 1000, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        logger.warning("upstox_ltt_parse_failed instrument_key=%s raw=%s", instrument_key, ltt_raw)
        return None


@dataclass
class Tick:
    """One symbol's latest known market state, in Upstox's own units -- translated to
    `core.broker_adapter.NormalizedTick` by `core/upstox_adapter.py` (same split as
    Kotak's `Tick`/`KotakAdapter`)."""

    symbol: str
    instrument_key: str
    ltp: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    # From Upstox's own `ltt` field -- a genuine broker-reported timestamp, unlike
    # Kotak's untouched Tick class (see core/kotak_adapter.py's _to_normalized
    # docstring for why that one is honestly None instead).
    exchange_ts: Optional[datetime] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))  # ingest time

    def update_from_feed(self, feed: dict) -> None:
        """`feed` is one entry from `FeedResponse.feeds[instrument_key]`, already
        protobuf-decoded to a plain dict by the SDK's own `handle_message`. Reads the
        "full" mode shape (`marketFF`/`indexFF`) confirmed via the compiled protobuf
        descriptor -- see this module's docstring."""
        full_feed = feed.get("fullFeed") or {}
        market_ff = full_feed.get("marketFF") or full_feed.get("indexFF") or {}
        ltpc = market_ff.get("ltpc") or {}

        if ltpc.get("ltp") not in (None, ""):
            self.ltp = float(ltpc["ltp"])
        if ltpc.get("cp") not in (None, ""):
            self.close = float(ltpc["cp"])
        parsed_ts = _extract_exchange_ts(feed, self.instrument_key)
        if parsed_ts is not None:
            self.exchange_ts = parsed_ts

        ohlc_list = ((market_ff.get("marketOHLC") or {}).get("ohlc")) or []
        if ohlc_list:
            # Multiple candle intervals may be present (1m/30m/1d per the SDK's
            # README); the first entry is used as-is -- exact interval-code semantics
            # are not yet confirmed against real live data (flagged in
            # BROKER_ARCHITECTURE.md for verification at first live run).
            candle = ohlc_list[0]
            if candle.get("open") not in (None, ""):
                self.open = float(candle["open"])
            if candle.get("high") not in (None, ""):
                self.high = float(candle["high"])
            if candle.get("low") not in (None, ""):
                self.low = float(candle["low"])
            if candle.get("vol") not in (None, ""):
                self.volume = int(float(candle["vol"]))

        bid_ask = ((market_ff.get("marketLevel") or {}).get("bidAskQuote")) or []
        if bid_ask:
            top = bid_ask[0]
            if top.get("bidP") not in (None, ""):
                self.bid = float(top["bidP"])
            if top.get("askP") not in (None, ""):
                self.ask = float(top["askP"])

        self.timestamp = datetime.now(timezone.utc)


def _extract_search_results(response) -> list:
    """Normalizes `InstrumentsApi.search_instrument`'s response into a plain list of
    dict-like items, defensively -- `SearchInstrumentResponse.data` is untyped
    (`object`) in the installed SDK's own model, so its exact runtime shape (a bare
    list vs. a dict wrapping a list under e.g. "instruments") is not yet confirmed
    against one real live call. Returns an empty list (never raises) if the shape is
    unrecognized -- resolution then fails loudly via InstrumentResolutionError at the
    call site, rather than this helper silently guessing."""
    data = getattr(response, "data", None)
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("instruments", "results", "data"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _item_get(item, field_name: str):
    if isinstance(item, dict):
        return item.get(field_name)
    return getattr(item, field_name, None)


class _InstrumentKeyRegistry:
    """symbol -> Upstox instrument_key, via the real `InstrumentsApi.search_instrument`
    REST endpoint. In-memory only (not disk-cached like Kotak's Scrip Master CSV),
    since this is a live per-query REST search, not a bulk downloadable file."""

    def __init__(self, api_client) -> None:
        self._api_client = api_client
        self._cache: dict[str, str] = {}

    def resolve(self, bare_symbol: str, exchange_segment: str, is_index: bool) -> str:
        cache_key = f"{exchange_segment}:{bare_symbol.upper()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        from upstox_client.api.instruments_api import InstrumentsApi

        search_term = _INDEX_SEARCH_ALIASES.get(bare_symbol.upper(), bare_symbol) if is_index else bare_symbol
        api = InstrumentsApi(self._api_client)
        # Confirmed live (2026-07-16): Upstox's search_instrument REST endpoint
        # rejects "NSE_INDEX"/"BSE_INDEX" as `exchanges` filter values
        # (errorCode UDAPI1171 "Invalid exchanges.") even though those exact strings
        # are valid instrument_key *prefixes* (e.g. "NSE_INDEX|Nifty 50") -- the two
        # are different constrained value sets in Upstox's real API, not
        # interchangeable as this code originally assumed. Indices are searched
        # without an exchanges filter and matched by name instead; equities keep the
        # filter (not yet found broken for that path).
        search_kwargs = {} if is_index else {"exchanges": exchange_segment}
        try:
            response = api.search_instrument(query=search_term, **search_kwargs)
        except Exception as exc:
            raise InstrumentResolutionError(
                f"search_instrument({search_term!r}, kwargs={search_kwargs!r}) failed: {_safe_str(exc)}"
            ) from exc

        results = _extract_search_results(response)
        upper_symbol = bare_symbol.upper()
        match = None
        for item in results:
            trading_symbol = _item_get(item, "trading_symbol")
            name = _item_get(item, "name")
            if is_index and name and str(name).upper() == search_term.upper():
                match = item
                break
            if not is_index and trading_symbol and str(trading_symbol).upper() == upper_symbol:
                match = item
                break
        if match is None and results:
            match = results[0]  # best-effort fallback from a real search -- never a guessed token
        if match is None:
            raise InstrumentResolutionError(
                f"No instrument_key found for {bare_symbol!r} in {exchange_segment!r} "
                f"({len(results)} result(s) searched) -- refusing to guess."
            )

        instrument_key = _item_get(match, "instrument_key")
        if not instrument_key:
            raise InstrumentResolutionError(f"Matched instrument for {bare_symbol!r} has no instrument_key field: {match!r}")
        self._cache[cache_key] = instrument_key
        return instrument_key


class UpstoxMarketDataService:
    """Singleton: one `MarketDataStreamerV3`, one WebSocket connection, for the whole
    process. Same threading/singleton discipline as `KotakMarketDataService` -- see
    that class's docstring for why `__new__`-based singleton (not `st.cache_resource`)
    is what gives one shared connection across every Streamlit page/session.
    """

    _instance: "UpstoxMarketDataService | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "UpstoxMarketDataService":
        with cls._instance_lock:
            if cls._instance is None:
                inst = super().__new__(cls)
                inst._initialized = False
                cls._instance = inst
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._lock = threading.RLock()
        self._streamer = None
        self._api_client = None
        self._connected = False
        self._authenticated = False
        self._status = STATUS_STOPPED
        self._last_error: Optional[str] = None
        # symbol -> {"instrument_key"}
        self._subscriptions: dict[str, dict] = {}
        self._ticks: dict[str, Tick] = {}
        self._reconnect_attempt = 0
        self._stop_requested = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._registry: Optional[_InstrumentKeyRegistry] = None
        # Every tick passes through this guard before touching self._ticks (see
        # _on_message) -- Upstox's real ltt-derived exchange_ts (unlike Kotak's
        # honestly-unavailable one) is what makes duplicate/out-of-order detection on
        # this adapter meaningful, not just a timestamp-ordering placeholder.
        self._sequence_guard = TickSequenceGuard()

    # --- credentials ---------------------------------------------------------

    @staticmethod
    def _missing_credentials() -> list[str]:
        return [name for name, val in [("UPSTOX_ANALYTICS_TOKEN", UPSTOX_ANALYTICS_TOKEN)] if not val]

    def credentials_configured(self) -> bool:
        return not self._missing_credentials()

    def status(self) -> dict:
        with self._lock:
            return {
                "status": self._status,
                "authenticated": self._authenticated,
                "connected": self._connected,
                "subscriptions": sorted(self._subscriptions.keys()),
                "last_error": self._last_error,
                "reconnect_attempt": self._reconnect_attempt,
                "sequence_counters": dict(self._sequence_guard.counters),
            }

    def get_tick(self, symbol: str) -> Optional[Tick]:
        with self._lock:
            return self._ticks.get(symbol.strip().upper())

    def all_ticks(self) -> dict[str, Tick]:
        with self._lock:
            return dict(self._ticks)

    # --- lifecycle ---------------------------------------------------------

    def ensure_started(self) -> None:
        """Idempotent, same contract as KotakMarketDataService.ensure_started()."""
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._stop_requested = False
            self._monitor_thread = threading.Thread(target=self._run_monitor, name="upstox-market-data-monitor", daemon=True)
            self._monitor_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_requested = True
            if self._streamer is not None:
                try:
                    self._streamer.disconnect()
                except Exception as exc:
                    logger.warning("upstox_disconnect_failed error=%s", _safe_str(exc))
            self._connected = False
            self._authenticated = False
            self._status = STATUS_STOPPED
        logger.info("upstox_market_data_stopped")

    def _run_monitor(self) -> None:
        """Background supervisor, same shape as KotakMarketDataService._run_monitor:
        authenticate, restore subscriptions, and on any disconnect/error, retry with
        exponential backoff (capped at MAX_BACKOFF_SECONDS) until stop() is called.
        Deliberately drives its own backoff rather than the SDK's built-in fixed-
        interval `auto_reconnect` (see this module's docstring)."""
        while True:
            with self._lock:
                if self._stop_requested:
                    return
            try:
                self._authenticate()
                with self._lock:
                    self._reconnect_attempt = 0
                try:
                    self.restore_subscriptions()
                except InstrumentResolutionError as exc:
                    logger.error("upstox_restore_subscriptions_failed error=%s", exc)

                was_ever_connected = False
                while True:
                    with self._lock:
                        if self._stop_requested:
                            return
                        still_connected = self._connected
                    if still_connected:
                        was_ever_connected = True
                    elif was_ever_connected:
                        break
                    time.sleep(2)
            except (UpstoxCredentialsError, UpstoxAuthError) as exc:
                with self._lock:
                    self._status = STATUS_FAILED
                    self._last_error = _safe_str(exc)
                logger.error("upstox_auth_fatal error=%s", _safe_str(exc))
                return
            except Exception as exc:
                with self._lock:
                    self._status = STATUS_RECONNECTING
                    self._last_error = _safe_str(exc)
                logger.error("upstox_connection_failed error=%s", _safe_str(exc))
                logger.warning("Reconnect reason:\n%s", type(exc).__name__)

            with self._lock:
                if self._stop_requested:
                    return
                self._reconnect_attempt += 1
                backoff = min(INITIAL_BACKOFF_SECONDS * (2 ** (self._reconnect_attempt - 1)), MAX_BACKOFF_SECONDS)
            logger.warning("upstox_reconnect_backoff attempt=%d seconds=%d", self._reconnect_attempt, backoff)
            time.sleep(backoff)

    # --- auth ---------------------------------------------------------

    def _authenticate(self) -> None:
        missing = self._missing_credentials()
        if missing:
            raise UpstoxCredentialsError(f"Missing required Upstox credential(s) in .env: {', '.join(missing)}")

        import upstox_client  # imported lazily so a missing SDK fails here, not at module import time

        with self._lock:
            self._status = STATUS_AUTHENTICATING

        configuration = upstox_client.Configuration(sandbox=False)
        configuration.access_token = UPSTOX_ANALYTICS_TOKEN
        api_client = upstox_client.ApiClient(configuration)

        streamer = upstox_client.MarketDataStreamerV3(api_client, [], "full")
        streamer.auto_reconnect(False)  # this service drives its own backoff instead -- see module docstring
        streamer.on("open", self._on_open)
        streamer.on("close", self._on_close)
        streamer.on("error", self._on_error)
        streamer.on("message", self._on_message)

        with self._lock:
            self._api_client = api_client
            self._streamer = streamer
            self._registry = _InstrumentKeyRegistry(api_client)
            self._authenticated = True
            self._status = STATUS_AUTHENTICATED
        logger.info("upstox_auth_configured")  # no login call for a pre-issued token -- see module docstring

        streamer.connect()

    # --- websocket callbacks ---------------------------------------------------------

    def _on_open(self) -> None:
        with self._lock:
            self._connected = True
            self._status = STATUS_CONNECTED if not self._subscriptions else STATUS_SUBSCRIBED
        logger.info("upstox_ws_open")

    def _on_close(self, *args) -> None:
        with self._lock:
            self._connected = False
            self._status = STATUS_RECONNECTING
        logger.warning("upstox_ws_close args=%s", args)
        logger.warning("Reconnect reason:\n%s", "SocketClosed")

    def _on_error(self, error) -> None:
        error_text = _safe_str(error) if isinstance(error, BaseException) else str(error)
        with self._lock:
            self._connected = False
            self._last_error = error_text
            self._status = STATUS_RECONNECTING
        logger.error("upstox_ws_error error=%s", error_text)
        if "401" in error_text:
            # Matches the SDK's own handle_error 401 detection (streamer.py) -- a
            # non-retriable auth failure, translated by core/upstox_adapter.py into
            # BrokerErrorType.AUTH_EXPIRED for the caller.
            raise UpstoxAuthError(f"Upstox access token rejected: {error_text}")
        logger.warning("Reconnect reason:\n%s", type(error).__name__ if isinstance(error, BaseException) else "WebSocketError")

    def _on_message(self, message: dict) -> None:
        received_at = time.monotonic()
        if not isinstance(message, dict):
            return
        feeds = message.get("feeds") or {}
        with self._lock:
            for instrument_key, feed in feeds.items():
                symbol = self._symbol_for_instrument_key(instrument_key)
                if symbol is None:
                    continue

                exchange_ts = _extract_exchange_ts(feed, instrument_key)
                # The sequence guard runs before this tick ever touches self._ticks
                # (see core/tick_sequence.py) -- a tick with no parseable exchange_ts
                # (e.g. a market_info-only message with no ltpc) always ACCEPTs, since
                # there's nothing to order against; a DUPLICATE/OUT_OF_ORDER verdict
                # only ever fires when a real timestamp was actually compared.
                if exchange_ts is not None:
                    decision = self._sequence_guard.evaluate(symbol, exchange_ts)
                    if decision.outcome in (TickOutcome.DUPLICATE, TickOutcome.OUT_OF_ORDER):
                        continue

                tick = self._ticks.get(symbol)
                if tick is None:
                    tick = Tick(symbol=symbol, instrument_key=instrument_key)
                    self._ticks[symbol] = tick
                tick.update_from_feed(feed)
                parse_and_cache_ms = (time.monotonic() - received_at) * 1000
                logger.info(
                    "upstox_tick_pipeline symbol=%s stage=received_to_cached duration_ms=%.2f ltp=%s",
                    symbol, parse_and_cache_ms, tick.ltp,
                )

    def _symbol_for_instrument_key(self, instrument_key: str) -> Optional[str]:
        for symbol, sub in self._subscriptions.items():
            if sub.get("instrument_key") == instrument_key:
                return symbol
        return None

    # --- symbol resolution ---------------------------------------------------------

    def _resolve_instrument_key(self, bare_symbol: str, exchange_segment: str, is_index: bool) -> str:
        if self._registry is None:
            raise UpstoxAuthError("Not authenticated yet -- call ensure_started() first")
        return self._registry.resolve(bare_symbol, exchange_segment, is_index)

    # --- subscriptions ---------------------------------------------------------

    def subscribe(self, symbol: str) -> None:
        self.subscribe_multiple([symbol])

    def subscribe_multiple(self, symbols: list[str]) -> None:
        """Same contract as KotakMarketDataService.subscribe_multiple: a symbol that
        fails to resolve is logged and skipped, never aborts the batch or the
        authenticated session; a single aggregated InstrumentResolutionError is raised
        at the end if any symbol failed."""
        if self._streamer is None or not self._authenticated:
            raise UpstoxAuthError("Not authenticated yet -- call ensure_started() first and wait for status()['authenticated']")

        keys_to_subscribe = []
        failed: dict[str, str] = {}
        with self._lock:
            for symbol in symbols:
                symbol = symbol.strip().upper()
                if symbol in self._subscriptions:
                    continue
                try:
                    bare_symbol, exchange_segment = _to_upstox_symbol(symbol)
                    is_index = symbol in INDEX_SYMBOLS
                    instrument_key = self._resolve_instrument_key(bare_symbol, exchange_segment, is_index)
                except (ValueError, InstrumentResolutionError) as exc:
                    logger.error("upstox_symbol_resolution_failed symbol=%s error=%s", symbol, exc)
                    failed[symbol] = str(exc)
                    continue
                self._subscriptions[symbol] = {"instrument_key": instrument_key}
                keys_to_subscribe.append(instrument_key)

        if keys_to_subscribe:
            logger.info("upstox_subscribe_request symbols=%s keys=%s", symbols, keys_to_subscribe)
            self._streamer.subscribe(keys_to_subscribe, "full")
            with self._lock:
                if self._connected:
                    self._status = STATUS_SUBSCRIBED

        if failed:
            raise InstrumentResolutionError(f"{len(failed)} symbol(s) failed to resolve: {failed}")

    def unsubscribe(self, symbol: str) -> None:
        self.unsubscribe_multiple([symbol])

    def unsubscribe_multiple(self, symbols: list[str]) -> None:
        if self._streamer is None:
            return
        with self._lock:
            to_remove = []
            for symbol in symbols:
                symbol = symbol.strip().upper()
                sub = self._subscriptions.get(symbol)
                if sub is None:
                    continue
                to_remove.append((symbol, sub))

        keys = [sub["instrument_key"] for _, sub in to_remove]
        if keys:
            self._streamer.unsubscribe(keys)

        with self._lock:
            for symbol, _ in to_remove:
                self._subscriptions.pop(symbol, None)
                self._ticks.pop(symbol, None)
        logger.info("upstox_unsubscribe symbols=%s", [s for s, _ in to_remove])

    def restore_subscriptions(self) -> None:
        with self._lock:
            symbols = list(self._subscriptions.keys())
            self._subscriptions.clear()
        if symbols:
            logger.info("upstox_restore_subscriptions symbols=%s", symbols)
            self.subscribe_multiple(symbols)


_service_singleton: Optional[UpstoxMarketDataService] = None


def get_market_data_service() -> UpstoxMarketDataService:
    """The one shared UpstoxMarketDataService instance for this process."""
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = UpstoxMarketDataService()
    return _service_singleton
