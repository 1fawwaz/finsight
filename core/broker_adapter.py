"""Live broker migration: the `BrokerAdapter` seam.

Every live-data source (Kotak Neo, Upstox) implements this identical interface so the
app can route to either behind one feature flag (`USE_UPSTOX_PRIMARY`) with zero
call-site changes anywhere else in the codebase -- see `core/kotak_adapter.py` and
`core/upstox_adapter.py` for the two implementations, and `BROKER_ARCHITECTURE.md`'s
EDR-1 for why this seam exists and EDR-2 for why it's this small (a full
Redis/OpenTelemetry/WebSocket-broadcast buildout was explicitly scoped down to match
this codebase's actual single-process Streamlit architecture).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from core.config import get_logger

logger = get_logger(__name__)


@dataclass
class NormalizedTick:
    """One canonical internal tick shape. Every broker-specific field mapping (e.g.
    Kotak's raw `ltp`/`tk`/`ltt` short keys) happens inside that broker's own adapter --
    nothing downstream of this point ever sees a broker-specific field name."""

    symbol: str
    ltp: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[int] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    # Broker-reported tick timestamp -- assumed clock-skewed, never trusted alone.
    exchange_ts: Optional[datetime] = None
    # This process's own receipt time. All latency math uses this, never exchange_ts.
    ingest_ts: Optional[datetime] = None
    # Populated only when the source broker's payload is confirmed (by reading its
    # real SDK/message shape, never assumed) to provide a true packet sequence number.
    sequence_id: Optional[int] = None


class BrokerErrorType(str, Enum):
    AUTH_EXPIRED = "AUTH_EXPIRED"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_NETWORK = "TRANSIENT_NETWORK"
    FATAL_PROTOCOL = "FATAL_PROTOCOL"
    SUBSCRIPTION_REJECTED = "SUBSCRIPTION_REJECTED"


class BrokerError(Exception):
    """Every adapter translates its broker-specific exceptions into one of these --
    downstream logic (reconnect policy, circuit breaker, alerting) branches on
    `error_type` only, never on a broker-specific exception class."""

    def __init__(self, error_type: BrokerErrorType, message: str) -> None:
        self.error_type = error_type
        super().__init__(f"[{error_type.value}] {message}")


class BrokerAdapter(ABC):
    """Common interface every live-data broker integration implements. Mirrors
    `core.kotak_market_data.KotakMarketDataService`'s existing public surface (that
    class's shape is what this interface was extracted from, not invented fresh) --
    `credentials_configured`/`ensure_started`/`stop`/`status`/`subscribe_multiple`/
    `unsubscribe_multiple`/`get_tick`/`all_ticks`/`restore_subscriptions`.
    """

    @property
    @abstractmethod
    def broker_name(self) -> str: ...

    @abstractmethod
    def credentials_configured(self) -> bool: ...

    @abstractmethod
    def ensure_started(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def subscribe_multiple(self, symbols: list[str]) -> None: ...

    @abstractmethod
    def unsubscribe_multiple(self, symbols: list[str]) -> None: ...

    @abstractmethod
    def get_tick(self, symbol: str) -> Optional[NormalizedTick]: ...

    @abstractmethod
    def all_ticks(self) -> dict[str, NormalizedTick]: ...

    @abstractmethod
    def restore_subscriptions(self) -> None: ...


def reload_broker_flags_from_env_file() -> None:
    """Re-parses `.env` into `os.environ` with `override=True`, so a raw file edit
    (not an OS-level environment variable change) becomes visible to `_read_flags`
    without restarting the process. Deliberately **not** called automatically by every
    flag read (see `_read_flags`) -- an innocuous "read a flag" call silently mutating
    global process environment on every invocation is surprising behavior and breaks
    test isolation (confirmed: doing this unconditionally made every router test that
    tries to simulate a different flag value via `monkeypatch` get silently overwritten
    by this repo's real `.env` file). Call this explicitly, once, after editing `.env`
    by hand and wanting the change picked up without a process restart.
    """
    from dotenv import load_dotenv

    load_dotenv(override=True)


def _read_flags() -> tuple[bool, bool]:
    """(use_upstox_primary, use_kotak_secondary), read fresh from `os.environ` on
    every call -- never the frozen `core.config.*` constants (computed once at process
    import time). Since this reads `os.environ` directly (not a cached/module-level
    value), any change to the *process environment* -- an OS-level env var set before
    Streamlit starts, or a deliberate `reload_broker_flags_from_env_file()` call after
    editing `.env` by hand -- takes effect on the very next call, with no process
    restart and no re-import. That is the real substance behind "rollback in under 60
    seconds": the flag read itself is instant; the only real-world delay is however the
    new value actually gets into the environment.
    """
    use_upstox = os.getenv("USE_UPSTOX_PRIMARY", "false").strip().lower() == "true"
    use_kotak_secondary = os.getenv("USE_KOTAK_SECONDARY", "false").strip().lower() == "true"
    return use_upstox, use_kotak_secondary


def get_active_broker_adapter() -> BrokerAdapter:
    """The primary live-data adapter for this process, chosen by `USE_UPSTOX_PRIMARY`.
    This is the entire feature-flag rollback mechanism -- see `_read_flags`."""
    use_upstox, _ = _read_flags()
    if use_upstox:
        from core.upstox_adapter import get_upstox_adapter

        return get_upstox_adapter()
    from core.kotak_adapter import get_kotak_adapter

    return get_kotak_adapter()


def get_secondary_broker_adapter() -> Optional[BrokerAdapter]:
    """Kotak Neo as the secondary/fallback adapter, gated by its own
    `USE_KOTAK_SECONDARY` flag -- independent of which adapter is primary. Returns
    `None` (not a fabricated adapter) when the secondary path is disabled, so a caller
    can distinguish "no secondary configured" from "secondary is Kotak."""
    _, use_kotak_secondary = _read_flags()
    if not use_kotak_secondary:
        return None
    from core.kotak_adapter import get_kotak_adapter

    return get_kotak_adapter()
