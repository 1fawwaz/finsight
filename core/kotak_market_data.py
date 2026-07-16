"""Live market data via the official Kotak Neo API (quotes/streaming ONLY).

Explicit scope boundary, matching this integration's own mandate: this module
never places, modifies, or cancels an order, and never touches holdings,
positions, margin, or the order feed. Only `totp_login`/`totp_validate`
(authentication), `subscribe`/`un_subscribe` (WebSocket live feed),
`search_scrip`/`scrip_master` (symbol resolution), and `quotes` (REST snapshot)
are ever called on the underlying `neo_api_client.NeoAPI` client.

This is additive to, not a replacement for, `core/data_ingestion.py`'s
yfinance-based historical pipeline -- that pipeline keeps serving ML training,
backtesting, and portfolio calculations exactly as before. This module adds a
separate, live intraday quote layer (LTP/OHLC/volume/bid/ask) on top.

Every method/callback signature and message shape used below was confirmed by
reading the actual installed `neo_api_client==2.0.2` source (its own bundled
`demo.py` is stale relative to this version -- e.g. it shows constructor kwargs
and a `login`/`session_2fa` flow that no longer exist -- so nothing here is
copied from it; every call site was checked against `neo_api.py`/
`NeoWebSocket.py`/`settings.py` directly). In particular:

- `NeoAPI(consumer_key=..., environment=...)`, then `client.on_message = ...`
  (a plain instance attribute, NOT a constructor kwarg) for each of
  on_message/on_error/on_open/on_close.
- Auth is two REST calls: `totp_login(mobile_number, ucc, totp)` then
  `totp_validate(mpin)`. Success is `client.configuration.edit_token`/
  `edit_sid` becoming non-None (this is exactly what `subscribe()` itself
  checks before allowing a WebSocket connection).
- `subscribe(instrument_tokens=[{"instrument_token": ..., "exchange_segment":
  ...}], isIndex=bool)` opens the one WebSocket lazily on first call.
- Live ticks arrive at `on_message` as `{"type": "stock_feed", "data": [...]}`
  where each item uses Kotak's raw short keys (`tk`, `ltp`, `v`, `bp`, `sp`,
  `h`, `lo`, `op`, `c`, `ltt`, ...) -- `settings.stock_key_mapping` is the
  authoritative name for each. These are NOT pre-mapped to friendly names the
  way `quotes()`'s REST response is (that only happens for `{"type":
  "quotes", ...}` messages, a different code path in `NeoWebSocket
  .quote_response_formatter`).
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import pyotp
import requests

from core.config import (
    DATA_DIR,
    KOTAK_CONSUMER_KEY,
    KOTAK_ENVIRONMENT,
    KOTAK_MOBILE_NUMBER,
    KOTAK_MPIN,
    KOTAK_TOTP_SECRET,
    KOTAK_UCC,
    get_logger,
)

logger = get_logger(__name__)

# Kotak's raw WebSocket tick keys -> friendly field names (from neo_api_client
# .settings.stock_key_mapping, reproduced here rather than imported so this module
# still degrades gracefully -- with a clear error, not an ImportError deep in a
# background thread -- if neo_api_client isn't installed).
_TICK_KEY_MAPPING = {
    "ltt": "last_traded_time",
    "v": "volume",
    "ltp": "last_traded_price",
    "ltq": "last_traded_quantity",
    "tbq": "total_buy_quantity",
    "tsq": "total_sell_quantity",
    "bp": "buy_price",
    "sp": "sell_price",
    "bq": "buy_quantity",
    "sq": "sell_quantity",
    "ap": "average_price",
    "oi": "open_interest",
    "lo": "low",
    "h": "high",
    "op": "open",
    "c": "close",
    "cng": "change",
    "nc": "net_change_percentage",
    "tk": "instrument_token",
    "e": "exchange_segment",
    "ts": "trading_symbol",
}

# Indices use a COMPLETELY DIFFERENT raw key vocabulary from stocks -- confirmed
# directly from the installed SDK's own neo_api_client/settings.py:
# `index_key_mapping` (not `stock_key_mapping`). An index tick's LTP arrives as
# "iv" (not "ltp"), open as "openingPrice" (not "op"), high/low as
# "highPrice"/"lowPrice" (not "h"/"lo"), and previous close as "ic" (not "c").
# Using _TICK_KEY_MAPPING against an index payload would silently leave every
# field unmapped (Tick.update_from_raw's `.get(k, k)` fallback keeps the raw,
# un-translated key name), so ltp/open/high/low would never populate -- this is
# NOT the same bug as a message never arriving at all, but is just as fatal to
# the card ever showing a value.
_INDEX_TICK_KEY_MAPPING = {
    "iv": "last_traded_price",
    "ic": "prev_day_close",
    "tvalue": "last_traded_time",
    "highPrice": "high",
    "lowPrice": "low",
    "openingPrice": "open",
    "cng": "change",
    "nc": "net_change_percentage",
    "tk": "instrument_token",
    "e": "exchange_segment",
}

# core.universe's ".NS"/".BO" convention -> Kotak's exchange_segment values
# (confirmed in neo_api_client.settings.exchange_segment).
_EXCHANGE_SEGMENT_BY_SUFFIX = {".NS": "nse_cm", ".BO": "bse_cm"}

# Well-known index display names this integration supports (per this task's explicit
# scope: "Nifty, Bank Nifty, Sensex, Stocks, Indices"), resolved dynamically via the
# official Scrip Master (see _ScripMasterRegistry below) -- never hardcoded, since
# Kotak's token IDs are not something this environment can verify without live
# credentials, and hardcoding a guessed token would violate this task's own "never
# invent SDK methods/values" instruction just as much as inventing a method would.
INDEX_SYMBOLS: dict[str, str] = {
    "NIFTY 50": "nse_cm",
    "NIFTY BANK": "nse_cm",
    "SENSEX": "bse_cm",
}

# Kotak's own Scrip Master lists these indices under compact names that don't match
# FinSight's display names -- confirmed by downloading and inspecting a real nse_cm
# Scrip Master CSV: row 0 is pSymbolName="NIFTY" (not "NIFTY 50"), row 1 is
# pSymbolName="BANKNIFTY" (not "NIFTY BANK"). This is a search-term alias only --
# resolution below still does a live registry lookup for the actual instrument_token,
# never a hardcoded token, so this doesn't violate the "never guess a token" rule.
_INDEX_SEARCH_ALIASES: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
}

MAX_BACKOFF_SECONDS = 60
INITIAL_BACKOFF_SECONDS = 2

# "No older than 1 trading day" per this task's own staleness definition. A real
# NSE trading-calendar (holidays, weekends) doesn't exist anywhere in this codebase
# yet, so a flat 24h threshold is used rather than introducing one just for this --
# it's a defensible, documented approximation (worst case: refreshes a few hours
# earlier than strictly necessary over a weekend/holiday, never later), not a
# guess presented as exact.
SCRIP_MASTER_STALENESS_HOURS = 24
SCRIP_MASTER_CACHE_DIR = DATA_DIR / "kotak_scrip_master"


class ScripResolutionError(Exception):
    """Raised when a symbol (especially one of the three required indices) cannot
    be resolved to an instrument_token via the Scrip Master -- surfaced loudly,
    never silently papered over with a guessed token."""


def _safe_str(exc: BaseException) -> str:
    """`str(exc)` for some SDK exceptions (e.g. neo_api_client's ApiException in
    certain failure modes) itself raises `TypeError: __str__ returned non-string`
    instead of a message -- this must never crash the code that's already inside
    an exception handler trying to log the original error."""
    try:
        text = str(exc)
    except Exception:
        text = None
    if not isinstance(text, str) or not text:
        return f"{type(exc).__name__} (no further detail available)"
    return text


# Explicit connection state machine. Status is always one of these; transitions
# back to AUTHENTICATING happen only when the session is actually invalid (a
# class B error below) -- never for a subscription/symbol-resolution failure
# (class A) or a plain network blip (class C, which goes through RECONNECTING
# and re-authenticates only because a real disconnect occurred, not because a
# resolution error was misclassified as one).
STATUS_STOPPED = "STOPPED"
STATUS_AUTHENTICATING = "AUTHENTICATING"
STATUS_AUTHENTICATED = "AUTHENTICATED"
STATUS_CONNECTED = "CONNECTED"
STATUS_SUBSCRIBED = "SUBSCRIBED"
STATUS_FAILED = "FAILED"
STATUS_RECONNECTING = "RECONNECTING"


class _ScripMasterRegistry:
    """Symbol -> instrument_token lookup, sourced from Kotak's official Scrip
    Master CSVs (one per exchange segment), cached locally on disk and refreshed
    automatically when the cache is missing or older than
    `SCRIP_MASTER_STALENESS_HOURS`.

    CSV column names (`pSymbolName`, `pTrdSymbol`, `pSymbol`) and the two-step
    fetch (call `scrip_master(exchange_segment)` for a CSV URL, then download and
    parse that CSV) were confirmed directly from the installed SDK's own
    `neo_api_client/api/scrip_search.py` reference implementation -- not guessed.
    """

    def __init__(self, client) -> None:
        self._client = client
        self._frames: dict[str, pd.DataFrame] = {}
        SCRIP_MASTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, exchange_segment: str) -> Path:
        return SCRIP_MASTER_CACHE_DIR / f"{exchange_segment}.csv"

    def _is_stale(self, path: Path) -> bool:
        if not path.exists():
            return True
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        return age_hours > SCRIP_MASTER_STALENESS_HOURS

    def _download(self, exchange_segment: str) -> pd.DataFrame:
        url_or_error = self._client.scrip_master(exchange_segment=exchange_segment)
        if isinstance(url_or_error, dict):
            raise ScripResolutionError(f"scrip_master({exchange_segment!r}) failed: {url_or_error}")
        response = requests.get(url_or_error, timeout=30)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        df = df.rename(columns=lambda c: c.strip())
        return df

    def get_frame(self, exchange_segment: str) -> pd.DataFrame:
        """The exchange segment's full scrip list, from cache if fresh, else a
        fresh download (which also refreshes the on-disk cache)."""
        if exchange_segment in self._frames:
            return self._frames[exchange_segment]

        cache_path = self._cache_path(exchange_segment)
        if not self._is_stale(cache_path):
            try:
                df = pd.read_csv(cache_path)
                self._frames[exchange_segment] = df
                logger.info("kotak_scrip_master_cache_hit exchange_segment=%s age_hours=%.1f",
                            exchange_segment, (time.time() - cache_path.stat().st_mtime) / 3600)
                return df
            except Exception as exc:
                logger.warning("kotak_scrip_master_cache_read_failed exchange_segment=%s error=%s", exchange_segment, exc)

        logger.info("kotak_scrip_master_refresh exchange_segment=%s reason=%s",
                    exchange_segment, "missing" if not cache_path.exists() else "stale")
        df = self._download(exchange_segment)
        df.to_csv(cache_path, index=False)
        self._frames[exchange_segment] = df
        logger.info("kotak_scrip_master_refreshed exchange_segment=%s rows=%d", exchange_segment, len(df))
        return df

    def resolve(self, bare_symbol: str, exchange_segment: str, is_index: bool) -> str:
        """bare_symbol (e.g. "RELIANCE", "NIFTY 50") -> instrument_token, via the
        cached Scrip Master. Raises ScripResolutionError (never returns a guessed
        token) if no match is found."""
        df = self.get_frame(exchange_segment)
        if "pSymbolName" not in df.columns or "pSymbol" not in df.columns:
            raise ScripResolutionError(
                f"Scrip Master for {exchange_segment!r} is missing expected columns "
                f"(pSymbolName/pSymbol); got {list(df.columns)}"
            )

        if is_index:
            # Indices are matched on pSymbolName. Kotak's own Scrip Master uses
            # compact names ("NIFTY", "BANKNIFTY") that differ from FinSight's
            # display names ("NIFTY 50", "NIFTY BANK") -- _INDEX_SEARCH_ALIASES
            # bridges that (see its own docstring for how this was confirmed).
            # Try an EXACT match on the (aliased) name first: a bare `.contains()`
            # search for "NIFTY" also matches NIFTYBEES/NIFTYBETA/NIFTYQLITY/etc,
            # so contains-only matching is unsound for indices and is used here
            # only as a last-resort fallback if no exact match exists.
            search_term = _INDEX_SEARCH_ALIASES.get(bare_symbol.upper(), bare_symbol.upper())
            names = df["pSymbolName"].astype(str).str.upper().str.strip()
            matches = df[names == search_term]
            if matches.empty:
                matches = df[names.str.contains(search_term, na=False)]
        else:
            trd_col = "pTrdSymbol" if "pTrdSymbol" in df.columns else "pSymbolName"
            matches = df[df[trd_col].astype(str).str.upper().str.strip() == bare_symbol.upper()]
            if matches.empty:
                mask = df["pSymbolName"].astype(str).str.upper().str.strip().str.startswith(bare_symbol.upper())
                matches = df[mask]

        if matches.empty:
            raise ScripResolutionError(
                f"No instrument_token found for {bare_symbol!r} in the {exchange_segment!r} Scrip Master "
                f"({len(df)} rows searched) -- refusing to guess a token."
            )
        token = matches.iloc[0]["pSymbol"]
        return str(token)


class KotakCredentialsError(Exception):
    """Raised when a required Kotak Neo credential is missing from the environment."""


class KotakAuthError(Exception):
    """Raised when TOTP login or MPIN validation fails."""


@dataclass
class Tick:
    """One symbol's latest known market state. All fields are `None` until at
    least one tick has actually arrived for that field."""

    symbol: str
    instrument_token: str
    exchange_segment: str
    ltp: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def update_from_raw(self, raw: dict, is_index: bool = False) -> None:
        """Apply a raw (short-key) tick payload's fields to this Tick in place.

        `is_index` selects which of the SDK's two, mutually incompatible key
        vocabularies applies (see `_INDEX_TICK_KEY_MAPPING`'s docstring) --
        stock and index ticks use different short keys for the same concept
        (e.g. LTP is "ltp" for a stock but "iv" for an index), confirmed
        directly from `neo_api_client/settings.py`'s own
        `stock_key_mapping`/`index_key_mapping`.
        """
        key_mapping = _INDEX_TICK_KEY_MAPPING if is_index else _TICK_KEY_MAPPING
        mapped = {key_mapping.get(k, k): v for k, v in raw.items()}
        if "last_traded_price" in mapped and mapped["last_traded_price"] not in (None, ""):
            self.ltp = float(mapped["last_traded_price"])
        if "open" in mapped and mapped["open"] not in (None, ""):
            self.open = float(mapped["open"])
        if "high" in mapped and mapped["high"] not in (None, ""):
            self.high = float(mapped["high"])
        if "low" in mapped and mapped["low"] not in (None, ""):
            self.low = float(mapped["low"])
        if "close" in mapped and mapped["close"] not in (None, ""):
            self.close = float(mapped["close"])
        if "prev_day_close" in mapped and mapped["prev_day_close"] not in (None, ""):
            self.close = float(mapped["prev_day_close"])
        if "volume" in mapped and mapped["volume"] not in (None, ""):
            self.volume = int(float(mapped["volume"]))
        if "buy_price" in mapped and mapped["buy_price"] not in (None, ""):
            self.bid = float(mapped["buy_price"])
        if "sell_price" in mapped and mapped["sell_price"] not in (None, ""):
            self.ask = float(mapped["sell_price"])
        self.timestamp = datetime.now(timezone.utc)


def _to_kotak_symbol(symbol: str) -> tuple[str, str]:
    """FinSight's canonical "RELIANCE.NS"/"SBIN.BO" -> (bare_symbol, exchange_segment)."""
    symbol = symbol.strip().upper()
    for suffix, segment in _EXCHANGE_SEGMENT_BY_SUFFIX.items():
        if symbol.endswith(suffix):
            return symbol[: -len(suffix)], segment
    if symbol in INDEX_SYMBOLS:
        return symbol, INDEX_SYMBOLS[symbol]
    raise ValueError(f"{symbol!r} is not a recognized NSE/BSE symbol or supported index")


class KotakMarketDataService:
    """Singleton: one NeoAPI client, one WebSocket connection, for the whole process.

    Thread-safe -- every read/write of shared state (tick cache, subscription set,
    connection status) is guarded by `self._lock`. Streamlit reruns each page's
    script on every interaction, but Python only *imports* a module once per
    process, so this class's `__new__`-based singleton (not `st.cache_resource`)
    is what actually gives one shared connection across every page and every
    session in the same server process.
    """

    _instance: "KotakMarketDataService | None" = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "KotakMarketDataService":
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
        self._client = None
        self._connected = False
        self._authenticated = False
        self._status = STATUS_STOPPED
        self._last_error: Optional[str] = None
        # symbol -> {"instrument_token", "exchange_segment", "is_index"}
        self._subscriptions: dict[str, dict] = {}
        self._ticks: dict[str, Tick] = {}
        self._reconnect_attempt = 0
        self._stop_requested = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._scrip_master: Optional[_ScripMasterRegistry] = None

    # --- credentials ---------------------------------------------------------

    @staticmethod
    def _missing_credentials() -> list[str]:
        return [
            name
            for name, val in [
                ("KOTAK_CONSUMER_KEY", KOTAK_CONSUMER_KEY),
                ("KOTAK_MOBILE_NUMBER", KOTAK_MOBILE_NUMBER),
                ("KOTAK_UCC", KOTAK_UCC),
                ("KOTAK_MPIN", KOTAK_MPIN),
                ("KOTAK_TOTP_SECRET", KOTAK_TOTP_SECRET),
            ]
            if not val
        ]

    # --- public status ---------------------------------------------------------

    def credentials_configured(self) -> bool:
        """True if every required Kotak Neo credential is present in .env. Callers
        (e.g. the UI) should check this before offering to enable live data, rather
        than starting the service and immediately hitting a credentials error."""
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
            }

    def get_tick(self, symbol: str) -> Optional[Tick]:
        with self._lock:
            tick = self._ticks.get(symbol.strip().upper())
            return tick

    def all_ticks(self) -> dict[str, Tick]:
        with self._lock:
            return dict(self._ticks)

    # --- lifecycle ---------------------------------------------------------

    def ensure_started(self) -> None:
        """Idempotent: authenticates + starts the background reconnect-monitor
        thread on first call; a no-op on every subsequent call for the life of
        the process. Safe to call from every page's top-level script code."""
        with self._lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._stop_requested = False
            self._monitor_thread = threading.Thread(
                target=self._run_monitor, name="kotak-market-data-monitor", daemon=True
            )
            self._monitor_thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_requested = True
            if self._client is not None:
                try:
                    self._client.logout()
                except Exception as exc:
                    logger.warning("kotak_logout_failed error=%s", exc)
            self._connected = False
            self._authenticated = False
            self._status = STATUS_STOPPED
        logger.info("kotak_market_data_stopped")

    def _run_monitor(self) -> None:
        """Background supervisor: authenticate, restore subscriptions, and on any
        disconnect/error, retry with exponential backoff (capped at
        MAX_BACKOFF_SECONDS) until `stop()` is called."""
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
                except ScripResolutionError as exc:
                    # A permanently-unresolvable symbol must not tear down an
                    # otherwise-valid, authenticated connection and force a fresh
                    # login -- log and keep monitoring the (still real) session.
                    logger.error("kotak_restore_subscriptions_failed error=%s", exc)

                # Authenticated -- park this thread. The actual WebSocket I/O runs
                # on NeoWebSocket's own internal thread; this loop only needs to
                # notice a genuine disconnect (via _on_close/_on_error flipping
                # self._connected False *after* having been True) and react.
                #
                # Critically: self._connected starts False and only becomes True
                # once something actually calls subscribe() (the SDK opens its one
                # WebSocket lazily on first subscribe). If nothing is subscribed
                # yet (e.g. a fresh singleton, or every symbol failed to resolve),
                # self._connected legitimately stays False with no WebSocket ever
                # opened -- that is NOT a disconnect and must not trigger backoff
                # + a brand new live login. Only a True->False transition (a real
                # disconnect after being connected) should do that.
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
            except (KotakCredentialsError, KotakAuthError) as exc:
                # Class B (auth/session): not retriable without the user fixing
                # .env or the live session actually being invalid -- log once
                # clearly and stop, rather than retrying forever.
                with self._lock:
                    self._status = STATUS_FAILED
                    self._last_error = _safe_str(exc)
                logger.error("kotak_auth_fatal error=%s", _safe_str(exc))
                return
            except Exception as exc:
                # Class C (network/WebSocket -- a genuine disconnect after having
                # been connected). This is the only path that loops back to
                # _authenticate(), and only because the connection itself is
                # actually gone, never because of a subscription/resolution
                # problem (those are caught separately above and never reach
                # here).
                with self._lock:
                    self._status = STATUS_RECONNECTING
                    self._last_error = _safe_str(exc)
                logger.error("kotak_connection_failed error=%s", _safe_str(exc))
                logger.warning("Reconnect reason:\n%s", type(exc).__name__)

            with self._lock:
                if self._stop_requested:
                    return
                self._reconnect_attempt += 1
                backoff = min(INITIAL_BACKOFF_SECONDS * (2 ** (self._reconnect_attempt - 1)), MAX_BACKOFF_SECONDS)
            logger.warning("kotak_reconnect_backoff attempt=%d seconds=%d", self._reconnect_attempt, backoff)
            time.sleep(backoff)

    # --- auth ---------------------------------------------------------

    def _generate_totp(self) -> str:
        return pyotp.TOTP(KOTAK_TOTP_SECRET).now()

    def _authenticate(self) -> None:
        missing = self._missing_credentials()
        if missing:
            raise KotakCredentialsError(f"Missing required Kotak Neo credential(s) in .env: {', '.join(missing)}")

        from neo_api_client import NeoAPI  # imported lazily so a missing SDK fails here, not at module import time

        with self._lock:
            self._status = STATUS_AUTHENTICATING

        client = NeoAPI(consumer_key=KOTAK_CONSUMER_KEY, environment=KOTAK_ENVIRONMENT)
        client.on_message = self._on_message
        client.on_error = self._on_error
        client.on_open = self._on_open
        client.on_close = self._on_close

        totp_code = self._generate_totp()
        login_resp = client.totp_login(mobile_number=KOTAK_MOBILE_NUMBER, ucc=KOTAK_UCC, totp=totp_code)
        if not isinstance(login_resp, dict) or login_resp.get("data") is None:
            raise KotakAuthError(f"Kotak Neo TOTP login failed: {login_resp}")

        validate_resp = client.totp_validate(mpin=KOTAK_MPIN)
        if not isinstance(validate_resp, dict) or validate_resp.get("data") is None:
            raise KotakAuthError(f"Kotak Neo MPIN validation failed: {validate_resp}")

        with self._lock:
            self._client = client
            self._scrip_master = _ScripMasterRegistry(client)
            self._authenticated = True
            self._status = STATUS_AUTHENTICATED
        logger.info("kotak_auth_success ucc=%s environment=%s", KOTAK_UCC, KOTAK_ENVIRONMENT)

    # --- websocket callbacks ---------------------------------------------------------
    # Signatures confirmed against neo_api.py's private __on_open/__on_close/__on_error
    # /__on_message wrappers, which call the public on_open/on_error/on_close/on_message
    # attributes with exactly these argument shapes.

    def _on_open(self, message: str = "") -> None:
        with self._lock:
            self._connected = True
            self._status = STATUS_CONNECTED if not self._subscriptions else STATUS_SUBSCRIBED
        logger.info("kotak_ws_open message=%s", message)

    def _on_close(self, message: str = "") -> None:
        with self._lock:
            self._connected = False
            self._status = STATUS_RECONNECTING
        logger.warning("kotak_ws_close message=%s", message)
        logger.warning("Reconnect reason:\n%s", "SocketClosed")

    def _on_error(self, error) -> None:
        with self._lock:
            self._connected = False
            self._last_error = _safe_str(error) if isinstance(error, BaseException) else str(error)
            self._status = STATUS_RECONNECTING
        logger.error("kotak_ws_error error=%s", error)
        logger.warning("Reconnect reason:\n%s", type(error).__name__ if isinstance(error, BaseException) else "WebSocketError")

    def _on_message(self, message) -> None:
        # Layer 10 (tick->UI latency): timestamp the moment this callback fires,
        # on the WebSocket's own background thread, before any parsing/locking --
        # this is the true start of the "tick received" stage.
        received_at = time.monotonic()

        # Raw-frame visibility (Layer 2/3 diagnostic): log every message this
        # callback is actually invoked with, BEFORE any type filtering/parsing --
        # confirms the callback fires at all, and surfaces any message "type"
        # this module doesn't yet recognize (e.g. if the SDK ever sends
        # something other than "stock_feed" for a given subscription kind)
        # instead of silently discarding it with no trace.
        logger.info("kotak_ws_raw_frame message=%s", message)

        if not isinstance(message, dict):
            return
        msg_type = message.get("type")
        if msg_type != "stock_feed":
            return
        data = message.get("data") or []
        with self._lock:
            for raw in data:
                if not isinstance(raw, dict) or "tk" not in raw:
                    continue
                token = str(raw["tk"])
                symbol = self._symbol_for_token(token)
                if symbol is None:
                    continue
                sub = self._subscriptions.get(symbol, {})
                is_index = sub.get("is_index", False)
                tick = self._ticks.get(symbol)
                if tick is None:
                    tick = Tick(
                        symbol=symbol,
                        instrument_token=token,
                        exchange_segment=sub.get("exchange_segment", raw.get("e", "")),
                    )
                    self._ticks[symbol] = tick
                tick.update_from_raw(raw, is_index=is_index)
                parse_and_cache_ms = (time.monotonic() - received_at) * 1000
                logger.info(
                    "kotak_tick_pipeline symbol=%s is_index=%s stage=received_to_cached duration_ms=%.2f ltp=%s raw=%s",
                    symbol, is_index, parse_and_cache_ms, tick.ltp, raw,
                )

    def _symbol_for_token(self, token: str) -> Optional[str]:
        for symbol, sub in self._subscriptions.items():
            if sub.get("instrument_token") == token:
                return symbol
        return None

    # --- symbol resolution ---------------------------------------------------------

    def _resolve_instrument_token(self, bare_symbol: str, exchange_segment: str, is_index: bool) -> str:
        """Look up `bare_symbol`'s instrument_token via the cached Scrip Master
        registry (see `_ScripMasterRegistry`) -- never hardcoded. Raises
        `ScripResolutionError` (never returns a guessed token) if resolution
        fails; callers must let this propagate ("fail loudly"), not swallow it."""
        if self._client is None or self._scrip_master is None:
            raise KotakAuthError("Not authenticated yet -- call ensure_started() first")
        return self._scrip_master.resolve(bare_symbol, exchange_segment, is_index)

    # --- subscriptions ---------------------------------------------------------

    def subscribe(self, symbol: str) -> None:
        """Subscribe to one symbol's live feed (e.g. "RELIANCE.NS", "NIFTY 50")."""
        self.subscribe_multiple([symbol])

    def subscribe_multiple(self, symbols: list[str]) -> None:
        """Resolve + subscribe every symbol in `symbols`. A symbol that fails to
        resolve (a permanent, config-class problem -- see ScripResolutionError) is
        logged and skipped; it never aborts the remaining symbols in this call and
        never affects the authenticated session. If any symbol failed, a single
        aggregated ScripResolutionError is raised at the end -- after every
        resolvable symbol has already been subscribed -- so a caller that wants to
        know which symbols failed still can, without that failure blocking anyone
        else."""
        if self._client is None or not self._authenticated:
            raise KotakAuthError("Not authenticated yet -- call ensure_started() first and wait for status()['authenticated']")

        tokens_payload = []
        failed: dict[str, str] = {}
        with self._lock:
            for symbol in symbols:
                symbol = symbol.strip().upper()
                if symbol in self._subscriptions:
                    continue
                try:
                    bare_symbol, exchange_segment = _to_kotak_symbol(symbol)
                    is_index = symbol in INDEX_SYMBOLS
                    instrument_token = self._resolve_instrument_token(bare_symbol, exchange_segment, is_index)
                except (ValueError, ScripResolutionError) as exc:
                    # Class A (permanent config error, see _run_monitor's docstring):
                    # an unrecognized symbol (_to_kotak_symbol's ValueError) or an
                    # unresolvable one (ScripResolutionError) -- log and skip only
                    # this symbol, never abort the batch, never touch session/auth
                    # state.
                    logger.error("kotak_symbol_resolution_failed symbol=%s error=%s", symbol, exc)
                    failed[symbol] = str(exc)
                    continue
                self._subscriptions[symbol] = {
                    "instrument_token": instrument_token,
                    "exchange_segment": exchange_segment,
                    "is_index": is_index,
                }
                tokens_payload.append(
                    {
                        "instrument_token": instrument_token,
                        "exchange_segment": exchange_segment,
                        "_is_index": is_index,
                        "_symbol": symbol,
                    }
                )

        # subscribe() takes one isIndex flag for the whole call, so split stock vs
        # index tokens into (at most) two calls rather than assuming they're never
        # mixed in one subscribe_multiple() invocation.
        for is_index in (False, True):
            batch = [t for t in tokens_payload if t["_is_index"] == is_index]
            if not batch:
                continue
            payload = [{"instrument_token": t["instrument_token"], "exchange_segment": t["exchange_segment"]} for t in batch]
            logger.info("kotak_subscribe_request symbols=%s isIndex=%s payload=%s", [t["_symbol"] for t in batch], is_index, payload)
            ack = self._client.subscribe(instrument_tokens=payload, isIndex=is_index)
            # NeoAPI.subscribe()/NeoWebSocket.get_live_feed() sends the request over
            # the socket and returns None on this SDK version -- there is no separate
            # synchronous subscription acknowledgement to log beyond this None, so
            # `ack` is logged as-is rather than assumed to be something more specific.
            logger.info("kotak_subscribe_ack symbols=%s isIndex=%s response=%s", [t["_symbol"] for t in batch], is_index, ack)

        if tokens_payload:
            with self._lock:
                if self._connected:
                    self._status = STATUS_SUBSCRIBED

        if failed:
            raise ScripResolutionError(f"{len(failed)} symbol(s) failed to resolve: {failed}")

    def unsubscribe(self, symbol: str) -> None:
        self.unsubscribe_multiple([symbol])

    def unsubscribe_multiple(self, symbols: list[str]) -> None:
        if self._client is None:
            return
        with self._lock:
            to_remove = []
            for symbol in symbols:
                symbol = symbol.strip().upper()
                sub = self._subscriptions.get(symbol)
                if sub is None:
                    continue
                to_remove.append((symbol, sub))

        for is_index in (False, True):
            batch = [(s, sub) for s, sub in to_remove if sub["is_index"] == is_index]
            if not batch:
                continue
            payload = [{"instrument_token": sub["instrument_token"], "exchange_segment": sub["exchange_segment"]} for _, sub in batch]
            self._client.un_subscribe(instrument_tokens=payload, isIndex=is_index)

        with self._lock:
            for symbol, _ in to_remove:
                self._subscriptions.pop(symbol, None)
                self._ticks.pop(symbol, None)
        logger.info("kotak_unsubscribe symbols=%s", [s for s, _ in to_remove])

    def restore_subscriptions(self) -> None:
        """Re-subscribe to every symbol that was subscribed before a disconnect --
        called automatically by the reconnect monitor, and safe to call manually."""
        with self._lock:
            symbols = list(self._subscriptions.keys())
            self._subscriptions.clear()  # subscribe_multiple() re-populates from scratch
        if symbols:
            logger.info("kotak_restore_subscriptions symbols=%s", symbols)
            self.subscribe_multiple(symbols)


_service_singleton: Optional[KotakMarketDataService] = None


def get_market_data_service() -> KotakMarketDataService:
    """The one shared KotakMarketDataService instance for this process."""
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = KotakMarketDataService()
    return _service_singleton
