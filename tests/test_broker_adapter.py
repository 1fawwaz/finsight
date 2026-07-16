"""Tests for core/broker_adapter.py -- the BrokerAdapter interface, NormalizedTick,
the error taxonomy, and the feature-flag router. The router tests inject fake
`core.kotak_adapter`/`core.upstox_adapter` modules via `sys.modules` so they can run
before those real modules exist (and without touching any live broker)."""

from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

from core.broker_adapter import (
    BrokerAdapter,
    BrokerError,
    BrokerErrorType,
    NormalizedTick,
    get_active_broker_adapter,
    get_secondary_broker_adapter,
)


class _FakeAdapter(BrokerAdapter):
    """A minimal, complete BrokerAdapter implementation -- proves the interface is
    actually implementable and exercised without any real broker/network."""

    def __init__(self, name: str = "fake"):
        self.name = name
        self._ticks: dict[str, NormalizedTick] = {}
        self._subscriptions: set[str] = set()
        self._started = False

    @property
    def broker_name(self) -> str:
        return self.name

    def credentials_configured(self) -> bool:
        return True

    def ensure_started(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def status(self) -> dict:
        return {"status": "STARTED" if self._started else "STOPPED", "subscriptions": sorted(self._subscriptions)}

    def subscribe_multiple(self, symbols: list[str]) -> None:
        self._subscriptions.update(s.upper() for s in symbols)

    def unsubscribe_multiple(self, symbols: list[str]) -> None:
        for s in symbols:
            self._subscriptions.discard(s.upper())
            self._ticks.pop(s.upper(), None)

    def get_tick(self, symbol: str):
        return self._ticks.get(symbol.upper())

    def all_ticks(self) -> dict:
        return dict(self._ticks)

    def restore_subscriptions(self) -> None:
        pass

    def push_tick(self, tick: NormalizedTick) -> None:
        self._ticks[tick.symbol.upper()] = tick


class TestNormalizedTick:
    def test_constructs_with_only_symbol_required(self):
        tick = NormalizedTick(symbol="RELIANCE.NS")
        assert tick.symbol == "RELIANCE.NS"
        assert tick.ltp is None
        assert tick.sequence_id is None

    def test_carries_both_exchange_ts_and_ingest_ts_independently(self):
        exchange_ts = datetime(2026, 7, 16, 9, 15, tzinfo=timezone.utc)
        ingest_ts = datetime(2026, 7, 16, 9, 15, 0, 250000, tzinfo=timezone.utc)
        tick = NormalizedTick(symbol="RELIANCE.NS", ltp=1300.5, exchange_ts=exchange_ts, ingest_ts=ingest_ts)
        assert tick.exchange_ts != tick.ingest_ts
        assert tick.ingest_ts > tick.exchange_ts


class TestBrokerError:
    def test_message_includes_error_type(self):
        err = BrokerError(BrokerErrorType.AUTH_EXPIRED, "session token expired")
        assert "AUTH_EXPIRED" in str(err)
        assert "session token expired" in str(err)
        assert err.error_type == BrokerErrorType.AUTH_EXPIRED

    def test_every_taxonomy_member_is_constructible(self):
        for error_type in BrokerErrorType:
            err = BrokerError(error_type, "test")
            assert err.error_type == error_type


class TestFakeAdapterConformance:
    def test_full_lifecycle(self):
        adapter = _FakeAdapter()
        assert adapter.credentials_configured() is True
        adapter.ensure_started()
        assert adapter.status()["status"] == "STARTED"
        adapter.subscribe_multiple(["reliance.ns"])
        assert "RELIANCE.NS" in adapter.status()["subscriptions"]
        adapter.push_tick(NormalizedTick(symbol="RELIANCE.NS", ltp=1300.0))
        assert adapter.get_tick("RELIANCE.NS").ltp == 1300.0
        assert "RELIANCE.NS" in adapter.all_ticks()
        adapter.unsubscribe_multiple(["RELIANCE.NS"])
        assert adapter.get_tick("RELIANCE.NS") is None
        adapter.stop()
        assert adapter.status()["status"] == "STOPPED"

    def test_cannot_instantiate_broker_adapter_directly(self):
        with pytest.raises(TypeError):
            BrokerAdapter()  # abstract -- must be subclassed


def _install_fake_broker_modules(monkeypatch, kotak_adapter, upstox_adapter):
    kotak_module = types.ModuleType("core.kotak_adapter")
    kotak_module.get_kotak_adapter = lambda: kotak_adapter
    upstox_module = types.ModuleType("core.upstox_adapter")
    upstox_module.get_upstox_adapter = lambda: upstox_adapter
    monkeypatch.setitem(sys.modules, "core.kotak_adapter", kotak_module)
    monkeypatch.setitem(sys.modules, "core.upstox_adapter", upstox_module)


class TestFeatureFlagRouter:
    def test_defaults_to_kotak_when_flag_unset(self, monkeypatch):
        monkeypatch.delenv("USE_UPSTOX_PRIMARY", raising=False)
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_active_broker_adapter() is kotak

    def test_routes_to_upstox_when_flag_true(self, monkeypatch):
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "true")
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_active_broker_adapter() is upstox

    def test_routes_to_kotak_when_flag_explicitly_false(self, monkeypatch):
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "false")
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_active_broker_adapter() is kotak

    def test_flag_is_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "TRUE")
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_active_broker_adapter() is upstox

    def test_flipping_the_flag_switches_routing_on_the_next_call_no_restart(self, monkeypatch):
        # This is the actual rollback SLO evidence: two calls, one env change between
        # them, no re-import, no process restart -- the second call routes differently.
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)

        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "false")
        first = get_active_broker_adapter()
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "true")
        second = get_active_broker_adapter()

        assert first is kotak
        assert second is upstox

    def test_secondary_is_none_when_disabled(self, monkeypatch):
        monkeypatch.delenv("USE_KOTAK_SECONDARY", raising=False)
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_secondary_broker_adapter() is None

    def test_secondary_returns_kotak_when_enabled(self, monkeypatch):
        monkeypatch.setenv("USE_KOTAK_SECONDARY", "true")
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_secondary_broker_adapter() is kotak

    def test_secondary_is_independent_of_primary_flag(self, monkeypatch):
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "true")
        monkeypatch.setenv("USE_KOTAK_SECONDARY", "true")
        kotak, upstox = _FakeAdapter("kotak"), _FakeAdapter("upstox")
        _install_fake_broker_modules(monkeypatch, kotak, upstox)
        assert get_active_broker_adapter() is upstox
        assert get_secondary_broker_adapter() is kotak


class TestRouterWithRealAdapterModules:
    """No injected fakes here -- proves the router genuinely resolves to the real
    KotakAdapter/UpstoxAdapter classes (construction only, no live connection is
    ever made by importing or instantiating an adapter)."""

    def test_flag_off_resolves_to_real_kotak_adapter(self, monkeypatch):
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "false")
        from core.kotak_adapter import KotakAdapter

        adapter = get_active_broker_adapter()
        assert isinstance(adapter, KotakAdapter)
        assert adapter.broker_name == "Kotak Neo"

    def test_flag_on_resolves_to_real_upstox_adapter(self, monkeypatch):
        monkeypatch.setenv("USE_UPSTOX_PRIMARY", "true")
        from core.upstox_adapter import UpstoxAdapter

        adapter = get_active_broker_adapter()
        assert isinstance(adapter, UpstoxAdapter)
        assert adapter.broker_name == "Upstox"
