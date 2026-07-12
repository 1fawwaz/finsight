"""Tests for core.fundamentals: cached, best-effort yfinance fundamentals snapshot."""

import pytest

import core.fundamentals as fundamentals_module
from core.fundamentals import Fundamentals, get_fundamentals


def _fake_ticker_with_info(info: dict):
    return lambda symbol: type("T", (), {"info": info})()


def test_get_fundamentals_returns_real_fields(monkeypatch):
    fundamentals_module.clear_cache()
    monkeypatch.setattr(
        fundamentals_module.yf,
        "Ticker",
        _fake_ticker_with_info(
            {
                "marketCap": 1_000_000_000,
                "trailingPE": 22.5,
                "dividendYield": 0.012,
                "fiftyTwoWeekHigh": 1500.0,
                "fiftyTwoWeekLow": 1100.0,
            }
        ),
    )
    result = get_fundamentals("RELIANCE.NS")
    assert result.available is True
    assert result.market_cap == 1_000_000_000
    assert result.pe_ratio == 22.5
    assert result.fifty_two_week_high == 1500.0
    assert result.fifty_two_week_low == 1100.0


def test_get_fundamentals_normalizes_percentage_style_dividend_yield(monkeypatch):
    # Regression: yfinance returned dividendYield=9.69 for a real ~9.69% yield (already
    # a percentage), not 0.0969 (a fraction). Without normalizing, downstream `:.1%}`
    # formatting would double-multiply this into an impossible "969.0%".
    fundamentals_module.clear_cache()
    monkeypatch.setattr(fundamentals_module.yf, "Ticker", _fake_ticker_with_info({"dividendYield": 9.69}))
    result = get_fundamentals("WIPRO.NS")
    assert result.dividend_yield == pytest.approx(0.0969)


def test_get_fundamentals_leaves_fraction_style_dividend_yield_unchanged(monkeypatch):
    fundamentals_module.clear_cache()
    monkeypatch.setattr(fundamentals_module.yf, "Ticker", _fake_ticker_with_info({"dividendYield": 0.0325}))
    result = get_fundamentals("SOMETICKER.NS")
    assert result.dividend_yield == pytest.approx(0.0325)


def test_get_fundamentals_missing_fields_are_none_not_fabricated(monkeypatch):
    fundamentals_module.clear_cache()
    monkeypatch.setattr(fundamentals_module.yf, "Ticker", _fake_ticker_with_info({}))
    result = get_fundamentals("SOMETICKER.NS")
    assert result.available is True
    assert result.market_cap is None
    assert result.pe_ratio is None


def test_get_fundamentals_network_failure_returns_unavailable_not_raise(monkeypatch):
    fundamentals_module.clear_cache()

    def _raise(symbol):
        raise ConnectionError("network down")

    monkeypatch.setattr(fundamentals_module.yf, "Ticker", _raise)
    result = get_fundamentals("RELIANCE.NS")
    assert result.available is False
    assert result.market_cap is None


def test_get_fundamentals_is_cached_within_ttl(monkeypatch):
    fundamentals_module.clear_cache()
    calls = {"count": 0}

    def _ticker(symbol):
        calls["count"] += 1
        return type("T", (), {"info": {"trailingPE": 20.0}})()

    monkeypatch.setattr(fundamentals_module.yf, "Ticker", _ticker)
    get_fundamentals("RELIANCE.NS")
    get_fundamentals("RELIANCE.NS")
    assert calls["count"] == 1


def test_get_fundamentals_cache_expires_after_ttl(monkeypatch):
    fundamentals_module.clear_cache()
    calls = {"count": 0}

    def _ticker(symbol):
        calls["count"] += 1
        return type("T", (), {"info": {"trailingPE": 20.0}})()

    monkeypatch.setattr(fundamentals_module.yf, "Ticker", _ticker)

    fake_time = [1000.0]
    monkeypatch.setattr(fundamentals_module.time, "monotonic", lambda: fake_time[0])

    get_fundamentals("RELIANCE.NS")
    fake_time[0] += fundamentals_module._CACHE_TTL_SECONDS + 1
    get_fundamentals("RELIANCE.NS")

    assert calls["count"] == 2
