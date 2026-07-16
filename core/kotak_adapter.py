"""Live broker migration: `BrokerAdapter` wrapper around the existing, unchanged
`KotakMarketDataService` (`core/kotak_market_data.py`). Purely a translation layer --
`Tick` -> `NormalizedTick`, `status()` shape -> the common interface -- the underlying
service's tested reconnect/auth/subscription logic is not touched by this migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from core.broker_adapter import BrokerAdapter, BrokerError, BrokerErrorType, NormalizedTick
from core.kotak_market_data import (
    KotakAuthError,
    KotakCredentialsError,
    KotakMarketDataService,
    ScripResolutionError,
    Tick,
    get_market_data_service,
)


def _to_normalized(tick: Tick) -> NormalizedTick:
    """`KotakMarketDataService.Tick.timestamp` is set to `datetime.now(UTC)` at parse
    time (see `Tick.update_from_raw`) -- it is genuinely an ingest timestamp, not a
    broker-reported exchange timestamp. Kotak's raw payload does carry an `ltt` field
    (mapped to `"last_traded_time"` in `_TICK_KEY_MAPPING`), but `update_from_raw`
    never actually parses it into any `Tick` field today -- confirmed by reading that
    method directly, not assumed. So `exchange_ts` is honestly reported as unavailable
    here (`None`) rather than fabricated from `tick.timestamp`, and `ingest_ts` reuses
    the one real receipt-time value the existing service already captures. Recorded as
    a Technical Debt Register item in BROKER_ARCHITECTURE.md (parsing `ltt` into a real
    exchange_ts would be a small, additive change to `Tick.update_from_raw`, deferred
    since this migration's own scope explicitly commits to not touching that file, and
    no current acceptance criterion needs it).

    `sequence_id` is always `None` -- Kotak's raw tick payload has no packet sequence
    field at all (confirmed against every key in `_TICK_KEY_MAPPING`).
    """
    return NormalizedTick(
        symbol=tick.symbol,
        ltp=tick.ltp,
        open=tick.open,
        high=tick.high,
        low=tick.low,
        close=tick.close,
        volume=tick.volume,
        bid=tick.bid,
        ask=tick.ask,
        exchange_ts=None,
        ingest_ts=tick.timestamp,
        sequence_id=None,
    )


class KotakAdapter(BrokerAdapter):
    """Thin `BrokerAdapter` facade over a `KotakMarketDataService` instance --
    dependency-injectable (`service=`) so tests never need the real singleton or a live
    connection; production code uses the no-arg form, which binds to the real,
    process-wide singleton via `get_market_data_service()`."""

    def __init__(self, service: Optional[KotakMarketDataService] = None) -> None:
        self._service = service if service is not None else get_market_data_service()

    @property
    def broker_name(self) -> str:
        return "Kotak Neo"

    def credentials_configured(self) -> bool:
        return self._service.credentials_configured()

    def ensure_started(self) -> None:
        self._service.ensure_started()

    def stop(self) -> None:
        self._service.stop()

    def status(self) -> dict:
        return self._service.status()

    def subscribe_multiple(self, symbols: list[str]) -> None:
        """Translates Kotak-specific exceptions into the common BrokerError taxonomy --
        same contract as core/upstox_adapter.py's UpstoxAdapter.subscribe_multiple, so
        UI code can catch BrokerError alone regardless of which adapter is active."""
        try:
            self._service.subscribe_multiple(symbols)
        except ScripResolutionError as exc:
            raise BrokerError(BrokerErrorType.SUBSCRIPTION_REJECTED, str(exc)) from exc
        except KotakAuthError as exc:
            raise BrokerError(BrokerErrorType.AUTH_EXPIRED, str(exc)) from exc
        except KotakCredentialsError as exc:
            raise BrokerError(BrokerErrorType.AUTH_EXPIRED, str(exc)) from exc

    def unsubscribe_multiple(self, symbols: list[str]) -> None:
        self._service.unsubscribe_multiple(symbols)

    def get_tick(self, symbol: str) -> Optional[NormalizedTick]:
        tick = self._service.get_tick(symbol)
        return _to_normalized(tick) if tick is not None else None

    def all_ticks(self) -> dict[str, NormalizedTick]:
        return {symbol: _to_normalized(tick) for symbol, tick in self._service.all_ticks().items()}

    def restore_subscriptions(self) -> None:
        self._service.restore_subscriptions()


_adapter_singleton: Optional[KotakAdapter] = None


def get_kotak_adapter() -> KotakAdapter:
    """The one shared KotakAdapter instance for this process -- wraps the same
    process-wide KotakMarketDataService singleton every other Kotak call site uses."""
    global _adapter_singleton
    if _adapter_singleton is None:
        _adapter_singleton = KotakAdapter()
    return _adapter_singleton
