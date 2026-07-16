"""Tests for core/upstox_market_data.py -- symbol/instrument-key mapping, tick
parsing from the real (confirmed) protobuf-decoded message shape, and the sequence
guard integration. No real Upstox connection is ever made -- every test either exercises
pure parsing logic or injects fakes for the SDK-touching pieces."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import core.upstox_market_data as upstox_market_data_module
from core.upstox_market_data import (
    INDEX_SYMBOLS,
    InstrumentResolutionError,
    Tick,
    UpstoxMarketDataService,
    _extract_exchange_ts,
    _extract_search_results,
    _to_upstox_symbol,
    get_market_data_service,
)


@pytest.fixture(autouse=True)
def _reset_upstox_singleton():
    """UpstoxMarketDataService is a process singleton (__new__-based, same pattern as
    KotakMarketDataService) -- every test in this file gets a genuinely fresh instance
    so subscription/tick/sequence-guard state never leaks between tests."""
    upstox_market_data_module.UpstoxMarketDataService._instance = None
    yield
    upstox_market_data_module.UpstoxMarketDataService._instance = None


class TestToUpstoxSymbol:
    def test_ns_suffix_maps_to_nse_eq(self):
        bare, segment = _to_upstox_symbol("RELIANCE.NS")
        assert bare == "RELIANCE"
        assert segment == "NSE_EQ"

    def test_bo_suffix_maps_to_bse_eq(self):
        bare, segment = _to_upstox_symbol("RELIANCE.BO")
        assert bare == "RELIANCE"
        assert segment == "BSE_EQ"

    def test_known_index_resolves_without_a_suffix(self):
        bare, segment = _to_upstox_symbol("NIFTY 50")
        assert bare == "NIFTY 50"
        assert segment == INDEX_SYMBOLS["NIFTY 50"]

    def test_unrecognized_symbol_raises(self):
        with pytest.raises(ValueError):
            _to_upstox_symbol("NOTAREALSTOCK")

    def test_case_and_whitespace_insensitive(self):
        bare, segment = _to_upstox_symbol("  reliance.ns  ")
        assert bare == "RELIANCE"
        assert segment == "NSE_EQ"


class TestExtractExchangeTs:
    def test_none_when_ltt_absent(self):
        assert _extract_exchange_ts({}, "NSE_EQ|X") is None

    def test_parses_epoch_milliseconds(self):
        # 2026-07-16T09:15:00Z in epoch ms
        epoch_ms = int(datetime(2026, 7, 16, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
        feed = {"fullFeed": {"marketFF": {"ltpc": {"ltt": str(epoch_ms)}}}}
        result = _extract_exchange_ts(feed, "NSE_EQ|X")
        assert result == datetime(2026, 7, 16, 9, 15, tzinfo=timezone.utc)

    def test_handles_index_feed_shape(self):
        epoch_ms = int(datetime(2026, 7, 16, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
        feed = {"fullFeed": {"indexFF": {"ltpc": {"ltt": str(epoch_ms)}}}}
        result = _extract_exchange_ts(feed, "NSE_INDEX|Nifty 50")
        assert result is not None

    def test_malformed_ltt_returns_none_not_a_crash(self):
        feed = {"fullFeed": {"marketFF": {"ltpc": {"ltt": "not-a-number"}}}}
        assert _extract_exchange_ts(feed, "NSE_EQ|X") is None


class TestTickUpdateFromFeed:
    def _feed(self, **overrides) -> dict:
        ltpc = {"ltp": "1300.50", "cp": "1295.00", "ltt": "1752650100000", **overrides.get("ltpc", {})}
        ohlc = overrides.get("ohlc", [{"open": "1290.0", "high": "1310.0", "low": "1285.0", "vol": "123456"}])
        bid_ask = overrides.get("bid_ask", [{"bidP": "1300.0", "askP": "1301.0"}])
        return {
            "fullFeed": {
                "marketFF": {
                    "ltpc": ltpc,
                    "marketOHLC": {"ohlc": ohlc},
                    "marketLevel": {"bidAskQuote": bid_ask},
                }
            }
        }

    def test_parses_ltp_and_close(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        tick.update_from_feed(self._feed())
        assert tick.ltp == 1300.50
        assert tick.close == 1295.00

    def test_parses_ohlc_and_volume(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        tick.update_from_feed(self._feed())
        assert tick.open == 1290.0
        assert tick.high == 1310.0
        assert tick.low == 1285.0
        assert tick.volume == 123456

    def test_parses_bid_ask(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        tick.update_from_feed(self._feed())
        assert tick.bid == 1300.0
        assert tick.ask == 1301.0

    def test_parses_real_exchange_timestamp(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        tick.update_from_feed(self._feed())
        assert tick.exchange_ts is not None

    def test_empty_ohlc_list_does_not_crash(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        tick.update_from_feed(self._feed(ohlc=[]))
        assert tick.open is None

    def test_missing_marketff_does_not_crash(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        tick.update_from_feed({"fullFeed": {}})
        assert tick.ltp is None

    def test_updates_the_real_ingest_timestamp(self):
        tick = Tick(symbol="RELIANCE.NS", instrument_key="NSE_EQ|X")
        before = tick.timestamp
        tick.update_from_feed(self._feed())
        assert tick.timestamp >= before


class TestExtractSearchResults:
    def test_none_data_returns_empty_list(self):
        class _Resp:
            data = None

        assert _extract_search_results(_Resp()) == []

    def test_bare_list_data_returned_as_is(self):
        class _Resp:
            data = [{"instrument_key": "NSE_EQ|X"}]

        assert _extract_search_results(_Resp()) == [{"instrument_key": "NSE_EQ|X"}]

    def test_dict_wrapped_list_is_unwrapped(self):
        class _Resp:
            data = {"instruments": [{"instrument_key": "NSE_EQ|X"}]}

        assert _extract_search_results(_Resp()) == [{"instrument_key": "NSE_EQ|X"}]

    def test_unrecognized_shape_returns_empty_list_not_a_crash(self):
        class _Resp:
            data = "unexpected string"

        assert _extract_search_results(_Resp()) == []


class TestServiceLifecycleWithoutCredentials:
    def test_missing_credentials_reports_not_configured(self, monkeypatch):
        monkeypatch.setattr("core.upstox_market_data.UPSTOX_ANALYTICS_TOKEN", "")
        service = UpstoxMarketDataService()
        assert service.credentials_configured() is False
        assert "UPSTOX_ANALYTICS_TOKEN" in service._missing_credentials()

    def test_subscribe_before_authentication_raises(self):
        service = UpstoxMarketDataService()
        service._streamer = None
        with pytest.raises(Exception):
            service.subscribe_multiple(["RELIANCE.NS"])


class TestSequenceGuardIntegration:
    def test_on_message_drops_a_duplicate_tick(self):
        service = UpstoxMarketDataService()
        service._subscriptions = {"RELIANCE.NS": {"instrument_key": "NSE_EQ|X"}}
        epoch_ms = int(datetime(2026, 7, 16, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
        feed = {"fullFeed": {"marketFF": {"ltpc": {"ltp": "1300.0", "ltt": str(epoch_ms)}}}}
        message = {"feeds": {"NSE_EQ|X": feed}}

        service._on_message(message)
        first_tick = service.get_tick("RELIANCE.NS")
        assert first_tick.ltp == 1300.0

        duplicate_feed = {"fullFeed": {"marketFF": {"ltpc": {"ltp": "9999.0", "ltt": str(epoch_ms)}}}}
        service._on_message({"feeds": {"NSE_EQ|X": duplicate_feed}})
        assert service.get_tick("RELIANCE.NS").ltp == 1300.0  # unchanged -- duplicate was dropped
        assert service.status()["sequence_counters"]["duplicate"] == 1

    def test_on_message_applies_a_genuinely_newer_tick(self):
        service = UpstoxMarketDataService()
        service._subscriptions = {"RELIANCE.NS": {"instrument_key": "NSE_EQ|X"}}
        t0 = int(datetime(2026, 7, 16, 9, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
        t1 = int(datetime(2026, 7, 16, 9, 15, 5, tzinfo=timezone.utc).timestamp() * 1000)

        service._on_message({"feeds": {"NSE_EQ|X": {"fullFeed": {"marketFF": {"ltpc": {"ltp": "1300.0", "ltt": str(t0)}}}}}})
        service._on_message({"feeds": {"NSE_EQ|X": {"fullFeed": {"marketFF": {"ltpc": {"ltp": "1301.5", "ltt": str(t1)}}}}}})

        assert service.get_tick("RELIANCE.NS").ltp == 1301.5
        assert service.status()["sequence_counters"]["accept"] == 2
