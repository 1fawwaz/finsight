"""Live broker migration: `BrokerAdapter` wrapper around
`UpstoxMarketDataService` (`core/upstox_market_data.py`). Purely a translation layer --
`Tick` -> `NormalizedTick`, `status()` shape -> the common interface -- same split as
`core/kotak_adapter.py`.
"""

from __future__ import annotations

from typing import Optional

from core.broker_adapter import BrokerAdapter, BrokerError, BrokerErrorType, NormalizedTick
from core.upstox_market_data import (
    InstrumentResolutionError,
    Tick,
    UpstoxAuthError,
    UpstoxCredentialsError,
    UpstoxMarketDataService,
    get_market_data_service,
)


def _to_normalized(tick: Tick) -> NormalizedTick:
    """Unlike Kotak's Tick (whose `timestamp` field is honestly ingest-only -- see
    core/kotak_adapter.py's docstring), Upstox's Tick.exchange_ts is a real
    broker-reported timestamp (parsed from `ltt`, confirmed in
    core/upstox_market_data.py's module docstring), so both dual-timestamp fields are
    genuinely populated here."""
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
        exchange_ts=tick.exchange_ts,
        ingest_ts=tick.timestamp,
        sequence_id=None,  # confirmed absent from the protobuf schema -- see module docstring
    )


class UpstoxAdapter(BrokerAdapter):
    """Thin `BrokerAdapter` facade over an `UpstoxMarketDataService` instance --
    dependency-injectable (`service=`) so tests never need the real singleton or a
    live connection; production code uses the no-arg form."""

    def __init__(self, service: Optional[UpstoxMarketDataService] = None) -> None:
        self._service = service if service is not None else get_market_data_service()

    @property
    def broker_name(self) -> str:
        return "Upstox"

    def credentials_configured(self) -> bool:
        return self._service.credentials_configured()

    def ensure_started(self) -> None:
        self._service.ensure_started()

    def stop(self) -> None:
        self._service.stop()

    def status(self) -> dict:
        return self._service.status()

    def subscribe_multiple(self, symbols: list[str]) -> None:
        try:
            self._service.subscribe_multiple(symbols)
        except InstrumentResolutionError as exc:
            raise BrokerError(BrokerErrorType.SUBSCRIPTION_REJECTED, str(exc)) from exc
        except UpstoxAuthError as exc:
            raise BrokerError(BrokerErrorType.AUTH_EXPIRED, str(exc)) from exc
        except UpstoxCredentialsError as exc:
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


_adapter_singleton: Optional[UpstoxAdapter] = None


def get_upstox_adapter() -> UpstoxAdapter:
    """The one shared UpstoxAdapter instance for this process -- wraps the same
    process-wide UpstoxMarketDataService singleton every other Upstox call site uses."""
    global _adapter_singleton
    if _adapter_singleton is None:
        _adapter_singleton = UpstoxAdapter()
    return _adapter_singleton
