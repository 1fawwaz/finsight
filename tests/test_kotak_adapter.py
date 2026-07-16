"""Tests for core/kotak_adapter.py -- the BrokerAdapter translation layer over
KotakMarketDataService. Every test injects a fake service (KotakAdapter's
`service=` constructor param) -- no real Kotak connection, no dependency on the
process-wide singleton."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.broker_adapter import BrokerError, BrokerErrorType
from core.kotak_adapter import KotakAdapter, get_kotak_adapter
from core.kotak_market_data import KotakAuthError, KotakCredentialsError, ScripResolutionError, Tick


class _FakeKotakService:
    """Duck-types just the surface KotakAdapter actually calls."""

    def __init__(self):
        self._ticks: dict[str, Tick] = {}
        self.started = False
        self.stopped = False
        self.raise_on_subscribe: Exception | None = None
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.restored = False

    def credentials_configured(self) -> bool:
        return True

    def ensure_started(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def status(self) -> dict:
        return {"status": "SUBSCRIBED", "subscriptions": list(self._ticks.keys())}

    def subscribe_multiple(self, symbols: list[str]) -> None:
        if self.raise_on_subscribe is not None:
            raise self.raise_on_subscribe
        self.subscribed.extend(symbols)

    def unsubscribe_multiple(self, symbols: list[str]) -> None:
        self.unsubscribed.extend(symbols)
        for s in symbols:
            self._ticks.pop(s.upper(), None)

    def get_tick(self, symbol: str):
        return self._ticks.get(symbol.upper())

    def all_ticks(self) -> dict:
        return dict(self._ticks)

    def restore_subscriptions(self) -> None:
        self.restored = True

    def push_tick(self, tick: Tick) -> None:
        self._ticks[tick.symbol.upper()] = tick


def _make_tick(symbol="RELIANCE.NS", ltp=1300.5) -> Tick:
    return Tick(symbol=symbol, instrument_token="12713", exchange_segment="nse_cm", ltp=ltp, timestamp=datetime.now(timezone.utc))


class TestKotakAdapterDelegation:
    def test_credentials_configured_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        assert adapter.credentials_configured() is True

    def test_ensure_started_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        adapter.ensure_started()
        assert service.started is True

    def test_stop_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        adapter.stop()
        assert service.stopped is True

    def test_status_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        assert adapter.status()["status"] == "SUBSCRIBED"

    def test_subscribe_multiple_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        adapter.subscribe_multiple(["RELIANCE.NS", "TCS.NS"])
        assert service.subscribed == ["RELIANCE.NS", "TCS.NS"]

    def test_unsubscribe_multiple_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        adapter.unsubscribe_multiple(["RELIANCE.NS"])
        assert service.unsubscribed == ["RELIANCE.NS"]

    def test_restore_subscriptions_delegates(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        adapter.restore_subscriptions()
        assert service.restored is True


class TestTickNormalization:
    def test_get_tick_returns_none_when_absent(self):
        service = _FakeKotakService()
        adapter = KotakAdapter(service=service)
        assert adapter.get_tick("RELIANCE.NS") is None

    def test_get_tick_normalizes_fields(self):
        service = _FakeKotakService()
        service.push_tick(_make_tick(ltp=1305.25))
        adapter = KotakAdapter(service=service)
        tick = adapter.get_tick("RELIANCE.NS")
        assert tick is not None
        assert tick.symbol == "RELIANCE.NS"
        assert tick.ltp == 1305.25

    def test_exchange_ts_is_honestly_none_not_fabricated(self):
        # Kotak's Tick.timestamp is an ingest time, not a broker-reported exchange
        # timestamp (see core/kotak_adapter.py's _to_normalized docstring) -- the
        # adapter must not pretend otherwise.
        service = _FakeKotakService()
        service.push_tick(_make_tick())
        adapter = KotakAdapter(service=service)
        tick = adapter.get_tick("RELIANCE.NS")
        assert tick.exchange_ts is None

    def test_ingest_ts_is_populated_from_the_real_tick_timestamp(self):
        service = _FakeKotakService()
        raw = _make_tick()
        service.push_tick(raw)
        adapter = KotakAdapter(service=service)
        tick = adapter.get_tick("RELIANCE.NS")
        assert tick.ingest_ts == raw.timestamp

    def test_sequence_id_is_always_none_for_kotak(self):
        service = _FakeKotakService()
        service.push_tick(_make_tick())
        adapter = KotakAdapter(service=service)
        assert adapter.get_tick("RELIANCE.NS").sequence_id is None

    def test_all_ticks_normalizes_every_entry(self):
        service = _FakeKotakService()
        service.push_tick(_make_tick("RELIANCE.NS", 1300.0))
        service.push_tick(_make_tick("TCS.NS", 3500.0))
        adapter = KotakAdapter(service=service)
        ticks = adapter.all_ticks()
        assert set(ticks.keys()) == {"RELIANCE.NS", "TCS.NS"}
        assert ticks["TCS.NS"].ltp == 3500.0


class TestSingleton:
    def test_get_kotak_adapter_returns_the_same_instance(self):
        assert get_kotak_adapter() is get_kotak_adapter()


class TestErrorTranslation:
    """Same BrokerError taxonomy contract as core/upstox_adapter.py's UpstoxAdapter --
    UI code must be able to catch BrokerError alone regardless of which adapter is
    active, never a broker-specific exception class."""

    def test_scrip_resolution_error_becomes_subscription_rejected(self):
        service = _FakeKotakService()
        service.raise_on_subscribe = ScripResolutionError("no match")
        adapter = KotakAdapter(service=service)
        with pytest.raises(BrokerError) as exc_info:
            adapter.subscribe_multiple(["UNKNOWN.NS"])
        assert exc_info.value.error_type == BrokerErrorType.SUBSCRIPTION_REJECTED

    def test_auth_error_becomes_auth_expired(self):
        service = _FakeKotakService()
        service.raise_on_subscribe = KotakAuthError("session invalid")
        adapter = KotakAdapter(service=service)
        with pytest.raises(BrokerError) as exc_info:
            adapter.subscribe_multiple(["RELIANCE.NS"])
        assert exc_info.value.error_type == BrokerErrorType.AUTH_EXPIRED

    def test_credentials_error_becomes_auth_expired(self):
        service = _FakeKotakService()
        service.raise_on_subscribe = KotakCredentialsError("missing credentials")
        adapter = KotakAdapter(service=service)
        with pytest.raises(BrokerError) as exc_info:
            adapter.subscribe_multiple(["RELIANCE.NS"])
        assert exc_info.value.error_type == BrokerErrorType.AUTH_EXPIRED
