"""Offline, no-network unit tests for core/kotak_market_data.py.

Covers the two production bugs found and fixed while wiring live Kotak Neo
ticks into the Home page's index cards:

1. Index symbol resolution ("NIFTY 50"/"NIFTY BANK" don't literally appear in
   Kotak's own Scrip Master, which uses "NIFTY"/"BANKNIFTY" -- and a naive
   `.str.contains()` search for "NIFTY" also matches NIFTYBEES/NIFTYBETA/etc).
2. The background monitor thread re-authenticating (a real, live broker login)
   in an unbounded loop whenever nothing was subscribed yet, because it
   misread "never connected because nothing subscribed" as "disconnected."

Nothing here touches the network or the real neo_api_client SDK.
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import MagicMock, patch

_real_sleep = time.sleep  # captured before any patching of core.kotak_market_data.time.sleep,
# since that module does `import time` -- the same module object this test file
# imports -- so patching its `.sleep` attribute patches the real global one too.

import pandas as pd
import pytest

from core.kotak_market_data import (
    KotakMarketDataService,
    ScripResolutionError,
    Tick,
    _safe_str,
    _ScripMasterRegistry,
    STATUS_RECONNECTING,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test gets its own fresh KotakMarketDataService -- the class is a
    process-wide singleton by design (see its own docstring), which is exactly
    right for the running app but would leak state between tests."""
    KotakMarketDataService._instance = None
    yield
    KotakMarketDataService._instance = None


def _fake_scrip_master_frame() -> pd.DataFrame:
    """A trimmed but realistic slice of a real downloaded nse_cm Scrip Master --
    same column names and the same "index rows use compact names, but lots of
    unrelated rows also contain the substring" shape that caused the original
    resolution bug."""
    return pd.DataFrame(
        [
            {"pSymbolName": "NIFTY", "pTrdSymbol": "NIFTY", "pSymbol": 26000},
            {"pSymbolName": "BANKNIFTY", "pTrdSymbol": "BANKNIFTY", "pSymbol": 26009},
            {"pSymbolName": "NIFTYBEES", "pTrdSymbol": "NIFTYBEES-EQ", "pSymbol": 10576},
            {"pSymbolName": "NIFTYBETA", "pTrdSymbol": "NIFTYBETA-EQ", "pSymbol": 10511},
            {"pSymbolName": "RELIANCE", "pTrdSymbol": "RELIANCE-EQ", "pSymbol": 12713},
        ]
    )


class TestIndexSymbolResolution:
    """Bug 1: "NIFTY 50"/"NIFTY BANK" must resolve to Kotak's real compact
    index names ("NIFTY"/"BANKNIFTY") via an exact match, not get confused by
    unrelated rows that merely contain "NIFTY" as a substring."""

    def test_nifty_50_resolves_to_the_exact_index_row_not_a_substring_match(self):
        registry = _ScripMasterRegistry(client=MagicMock())
        registry._frames["nse_cm"] = _fake_scrip_master_frame()

        token = registry.resolve("NIFTY 50", "nse_cm", is_index=True)

        assert token == "26000"

    def test_nifty_bank_resolves_to_the_exact_banknifty_row(self):
        registry = _ScripMasterRegistry(client=MagicMock())
        registry._frames["nse_cm"] = _fake_scrip_master_frame()

        token = registry.resolve("NIFTY BANK", "nse_cm", is_index=True)

        assert token == "26009"

    def test_unresolvable_index_raises_instead_of_guessing(self):
        registry = _ScripMasterRegistry(client=MagicMock())
        registry._frames["nse_cm"] = _fake_scrip_master_frame()

        with pytest.raises(ScripResolutionError):
            registry.resolve("NOT A REAL INDEX", "nse_cm", is_index=True)

    def test_stock_symbol_still_resolves_via_exact_trading_symbol_match(self):
        registry = _ScripMasterRegistry(client=MagicMock())
        registry._frames["nse_cm"] = _fake_scrip_master_frame()

        token = registry.resolve("RELIANCE", "nse_cm", is_index=False)

        assert token == "12713"


class TestScripMasterStalenessRefresh:
    """Layer 9: the on-disk Scrip Master cache must be reused while fresh and
    automatically re-downloaded once older than SCRIP_MASTER_STALENESS_HOURS --
    exercised here against a mocked client/HTTP response (no live SDK call)."""

    def test_fresh_cache_is_reused_without_calling_scrip_master(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.kotak_market_data.SCRIP_MASTER_CACHE_DIR", tmp_path)
        cache_path = tmp_path / "nse_cm.csv"
        _fake_scrip_master_frame().to_csv(cache_path, index=False)

        client = MagicMock()
        registry = _ScripMasterRegistry(client=client)

        df = registry.get_frame("nse_cm")

        assert len(df) == len(_fake_scrip_master_frame())
        client.scrip_master.assert_not_called()

    def test_stale_cache_triggers_a_fresh_download(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.kotak_market_data.SCRIP_MASTER_CACHE_DIR", tmp_path)
        cache_path = tmp_path / "nse_cm.csv"
        _fake_scrip_master_frame().to_csv(cache_path, index=False)

        # Back-date the cache file past SCRIP_MASTER_STALENESS_HOURS (24h).
        stale_time = time.time() - 25 * 3600
        os.utime(cache_path, (stale_time, stale_time))

        client = MagicMock()
        client.scrip_master.return_value = "https://example.invalid/scrip_master.csv"
        fresh_csv = _fake_scrip_master_frame().to_csv(index=False)
        fake_response = MagicMock(text=fresh_csv)
        fake_response.raise_for_status.return_value = None

        registry = _ScripMasterRegistry(client=client)
        with patch("core.kotak_market_data.requests.get", return_value=fake_response) as mock_get:
            df = registry.get_frame("nse_cm")

        client.scrip_master.assert_called_once_with(exchange_segment="nse_cm")
        mock_get.assert_called_once()
        assert len(df) == len(_fake_scrip_master_frame())

    def test_missing_cache_also_triggers_a_download(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.kotak_market_data.SCRIP_MASTER_CACHE_DIR", tmp_path)

        client = MagicMock()
        client.scrip_master.return_value = "https://example.invalid/scrip_master.csv"
        fresh_csv = _fake_scrip_master_frame().to_csv(index=False)
        fake_response = MagicMock(text=fresh_csv)
        fake_response.raise_for_status.return_value = None

        registry = _ScripMasterRegistry(client=client)
        with patch("core.kotak_market_data.requests.get", return_value=fake_response):
            df = registry.get_frame("nse_cm")

        client.scrip_master.assert_called_once_with(exchange_segment="nse_cm")
        assert len(df) == len(_fake_scrip_master_frame())


class TestSubscribeMultipleResilience:
    """Bug fix: one symbol failing to resolve must not block the other,
    resolvable symbols in the same subscribe_multiple() call."""

    def _authenticated_service(self) -> KotakMarketDataService:
        service = KotakMarketDataService()
        service._client = MagicMock()
        service._authenticated = True
        service._scrip_master = _ScripMasterRegistry(client=MagicMock())
        service._scrip_master._frames["nse_cm"] = _fake_scrip_master_frame()
        return service

    def test_one_bad_symbol_does_not_block_the_others(self):
        service = self._authenticated_service()

        # ZZZNOTREAL.NS is a well-formed NSE symbol (routes through _to_kotak_symbol
        # fine) that simply isn't in the (fake) Scrip Master -- a ScripResolutionError,
        # not a ValueError, exercising the same "one bad symbol" path.
        with pytest.raises(ScripResolutionError):
            service.subscribe_multiple(["NIFTY 50", "ZZZNOTREAL.NS", "NIFTY BANK"])

        # Both resolvable symbols were still subscribed despite the bad one.
        assert "NIFTY 50" in service._subscriptions
        assert "NIFTY BANK" in service._subscriptions
        assert "ZZZNOTREAL.NS" not in service._subscriptions
        assert service._client.subscribe.called

    def test_an_unrecognized_symbol_also_does_not_block_the_others(self):
        """Same guarantee, but for a symbol that isn't even a recognized NSE/BSE
        pattern or index (_to_kotak_symbol's ValueError) -- not just one the
        Scrip Master doesn't contain (ScripResolutionError)."""
        service = self._authenticated_service()

        with pytest.raises(ScripResolutionError):
            service.subscribe_multiple(["NIFTY 50", "NOT A REAL INDEX", "NIFTY BANK"])

        assert "NIFTY 50" in service._subscriptions
        assert "NIFTY BANK" in service._subscriptions
        assert "NOT A REAL INDEX" not in service._subscriptions

    def test_all_symbols_resolvable_raises_nothing(self):
        service = self._authenticated_service()

        service.subscribe_multiple(["NIFTY 50", "NIFTY BANK"])

        assert set(service._subscriptions.keys()) == {"NIFTY 50", "NIFTY BANK"}


class TestMonitorDoesNotReauthenticateWithoutACauseToReconnect:
    """Bug 2 (the critical safety bug): when nothing is subscribed yet, the
    WebSocket never opens and self._connected legitimately stays False. The
    monitor thread must treat that as "authenticated and idle," never as a
    disconnect requiring a fresh live login."""

    def test_no_repeated_authentication_when_nothing_is_subscribed(self):
        service = KotakMarketDataService()
        auth_calls = []

        def fake_authenticate():
            auth_calls.append(time.monotonic())
            service._authenticated = True

        with patch.object(service, "_authenticate", side_effect=fake_authenticate), patch.object(
            service, "restore_subscriptions", return_value=None
        ), patch("core.kotak_market_data.time.sleep", return_value=None):

            def stop_soon():
                # Let the monitor loop tick a handful of times, simulating
                # several seconds of real idle time, then stop it.
                for _ in range(5):
                    pass
                service._stop_requested = True

            # Run the monitor on its own thread (as production code does),
            # but flip _stop_requested almost immediately from here -- the
            # fake time.sleep patch means the inner "while connected" loop
            # spins fast, so a short real-time join is enough.
            thread = threading.Thread(target=service._run_monitor, daemon=True)
            thread.start()
            time.sleep(0.2)
            service._stop_requested = True
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert len(auth_calls) == 1, (
            f"Expected exactly one authentication attempt when nothing was ever "
            f"subscribed, got {len(auth_calls)} -- this is the exact bug that "
            f"caused repeated live logins against a real broker account."
        )

    def test_a_scrip_resolution_failure_during_restore_does_not_trigger_reauth(self):
        service = KotakMarketDataService()
        auth_calls = []

        def fake_authenticate():
            auth_calls.append(time.monotonic())
            service._authenticated = True

        with patch.object(service, "_authenticate", side_effect=fake_authenticate), patch.object(
            service, "restore_subscriptions", side_effect=ScripResolutionError("bad symbol")
        ), patch("core.kotak_market_data.time.sleep", return_value=None):
            thread = threading.Thread(target=service._run_monitor, daemon=True)
            thread.start()
            time.sleep(0.2)
            service._stop_requested = True
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert len(auth_calls) == 1, (
            "A ScripResolutionError raised while restoring subscriptions must be "
            "absorbed and logged, not treated as a connection failure that "
            "triggers a fresh re-authentication."
        )
        assert service._status != STATUS_RECONNECTING

    def test_a_genuine_disconnect_after_being_connected_does_reauthenticate(self):
        """The opposite case: once really connected, a real disconnect (_connected
        flips True -> False, e.g. via _on_close) must still trigger the normal
        reconnect-with-backoff-then-reauthenticate path."""
        service = KotakMarketDataService()
        auth_calls = []
        call_count = {"n": 0}

        def fake_authenticate():
            auth_calls.append(time.monotonic())
            service._authenticated = True
            call_count["n"] += 1
            if call_count["n"] == 1:
                service._connected = True  # simulate a successful subscribe -> WS open
            else:
                service._stop_requested = True  # stop after the second attempt

        def fake_restore():
            if call_count["n"] == 1:
                # Simulate the WS staying open briefly, then a real disconnect.
                def flip_disconnect():
                    _real_sleep(0.05)
                    service._connected = False

                threading.Thread(target=flip_disconnect, daemon=True).start()

        with patch.object(service, "_authenticate", side_effect=fake_authenticate), patch.object(
            service, "restore_subscriptions", side_effect=fake_restore
        ), patch("core.kotak_market_data.time.sleep", side_effect=lambda s: _real_sleep(min(s, 0.02))):
            thread = threading.Thread(target=service._run_monitor, daemon=True)
            thread.start()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert len(auth_calls) == 2, (
            f"Expected exactly two authentications: the first connection, then one "
            f"reconnect after a genuine disconnect. Got {len(auth_calls)}."
        )


class TestTickParsingUsesTheCorrectKeyVocabulary:
    """A third bug found only by reading the SDK's settings.py directly: stock
    and index ticks use ENTIRELY DIFFERENT raw short keys for the same concept
    (confirmed from neo_api_client.settings.stock_key_mapping vs
    index_key_mapping). Parsing an index tick with the stock mapping (or vice
    versa) silently leaves every field unset -- no exception, just a Tick that
    never populates."""

    def test_stock_tick_parses_via_stock_key_vocabulary(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_token="12713", exchange_segment="nse_cm")
        raw = {"tk": "12713", "e": "nse_cm", "ltp": "1310.5", "op": "1309.5", "h": "1315.0", "lo": "1305.0"}

        tick.update_from_raw(raw, is_index=False)

        assert tick.ltp == 1310.5
        assert tick.open == 1309.5
        assert tick.high == 1315.0
        assert tick.low == 1305.0

    def test_index_tick_parses_via_index_key_vocabulary_not_stock_keys(self):
        """The exact real-world payload shape for an index tick -- "iv" for LTP,
        "openingPrice"/"highPrice"/"lowPrice", not "ltp"/"op"/"h"/"lo"."""
        tick = Tick(symbol="NIFTY 50", instrument_token="26000", exchange_segment="nse_cm")
        raw = {
            "tk": "26000",
            "e": "nse_cm",
            "iv": "24300.25",
            "openingPrice": "24200.0",
            "highPrice": "24350.0",
            "lowPrice": "24190.0",
            "ic": "24206.90",
        }

        tick.update_from_raw(raw, is_index=True)

        assert tick.ltp == 24300.25
        assert tick.open == 24200.0
        assert tick.high == 24350.0
        assert tick.low == 24190.0
        assert tick.close == 24206.90

    def test_index_tick_parsed_with_the_stock_vocabulary_would_silently_fail(self):
        """Demonstrates the exact bug this test class exists to prevent: using
        the wrong (stock) mapping against a real index payload leaves ltp/open/
        high/low all None, with no error raised anywhere."""
        tick = Tick(symbol="NIFTY 50", instrument_token="26000", exchange_segment="nse_cm")
        raw = {
            "tk": "26000",
            "e": "nse_cm",
            "iv": "24300.25",
            "openingPrice": "24200.0",
            "highPrice": "24350.0",
            "lowPrice": "24190.0",
        }

        tick.update_from_raw(raw, is_index=False)  # deliberately wrong vocabulary

        assert tick.ltp is None
        assert tick.open is None
        assert tick.high is None
        assert tick.low is None


class TestSafeStr:
    """_safe_str must never itself raise, even for SDK exceptions whose own
    __str__ is broken (the exact TypeError seen against the real Kotak API)."""

    def test_normal_exception_message_is_preserved(self):
        assert _safe_str(ValueError("boom")) == "boom"

    def test_exception_with_broken_str_does_not_crash(self):
        class BrokenStr(Exception):
            def __str__(self):
                return None  # type: ignore[return-value]

        result = _safe_str(BrokenStr())
        assert isinstance(result, str)
        assert "BrokenStr" in result
