"""Tests for core.explain: plain-language Simple Mode text and Professional Mode detail."""

import re

import pytest

from core.explain import (
    Explanation,
    explain_adx,
    explain_atr,
    explain_bollinger,
    explain_diversification,
    explain_drawdown,
    explain_fundamentals,
    explain_macd,
    explain_ml_prediction,
    explain_resistance,
    explain_risk_level,
    explain_rsi,
    explain_sentiment,
    explain_sharpe,
    explain_support,
    explain_vwap,
    explain_volatility,
)

# Jargon that must never appear in Simple Mode text, per the "explain like I'm 10" bar.
BANNED_JARGON = [
    "rsi",
    "macd",
    "volatility",
    "diversification",
    "bollinger",
    "sharpe",
    "drawdown",
    "atr",
    "adx",
    "vwap",
    "overbought",
    "oversold",
]


def _assert_no_jargon(text: str) -> None:
    lowered = text.lower()
    for term in BANNED_JARGON:
        assert not re.search(rf"\b{term}\b", lowered), f"found jargon {term!r} in Simple Mode text: {text!r}"


@pytest.mark.parametrize(
    "value",
    [None, 15.0, 30.0, 50.0, 70.0, 85.0],
)
def test_explain_rsi_simple_text_has_no_jargon(value):
    result = explain_rsi(value)
    assert isinstance(result, Explanation)
    _assert_no_jargon(result.simple)


def test_explain_rsi_overbought_is_worried():
    assert explain_rsi(80.0).mood == "worried"


def test_explain_rsi_neutral_band():
    assert explain_rsi(50.0).mood == "neutral"


def test_explain_rsi_professional_includes_number():
    result = explain_rsi(72.3)
    assert "72" in result.professional


def test_explain_macd_bullish_is_good():
    result = explain_macd(macd_value=1.5, signal_value=1.0)
    assert result.mood == "good"
    _assert_no_jargon(result.simple)


def test_explain_macd_bearish_is_worried():
    result = explain_macd(macd_value=-1.5, signal_value=-1.0)
    assert result.mood == "worried"
    _assert_no_jargon(result.simple)


def test_explain_macd_none_values_handled_gracefully():
    result = explain_macd(None, None)
    _assert_no_jargon(result.simple)


def test_explain_support_resistance_no_jargon_and_relative_direction():
    support = explain_support(current_price=100.0, support=90.0)
    resistance = explain_resistance(current_price=100.0, resistance=110.0)
    _assert_no_jargon(support.simple)
    _assert_no_jargon(resistance.simple)
    assert "cheap" in support.simple.lower()
    assert "expensive" in resistance.simple.lower()


def test_explain_bollinger_extremes():
    upper_touch = explain_bollinger(current_price=110.0, upper=110.0, lower=90.0)
    lower_touch = explain_bollinger(current_price=90.0, upper=110.0, lower=90.0)
    middle = explain_bollinger(current_price=100.0, upper=110.0, lower=90.0)
    assert upper_touch.mood == "worried"
    assert middle.mood == "good"
    for r in (upper_touch, lower_touch, middle):
        _assert_no_jargon(r.simple)


def test_explain_volatility_high_is_worried_low_is_good():
    assert explain_volatility(0.55).mood == "worried"
    assert explain_volatility(0.10).mood == "good"
    _assert_no_jargon(explain_volatility(0.55).simple)


def test_explain_atr_scales_with_price():
    result = explain_atr(value=5.0, current_price=100.0)
    _assert_no_jargon(result.simple)
    assert "5.00" in result.simple or "5.0" in result.simple


def test_explain_adx_trending_vs_not():
    trending = explain_adx(30.0)
    ranging = explain_adx(10.0)
    _assert_no_jargon(trending.simple)
    _assert_no_jargon(ranging.simple)


def test_explain_vwap_above_and_below():
    above = explain_vwap(current_price=105.0, vwap_value=100.0)
    below = explain_vwap(current_price=95.0, vwap_value=100.0)
    _assert_no_jargon(above.simple)
    _assert_no_jargon(below.simple)
    assert below.mood == "good"


def test_explain_sharpe_good_and_bad():
    assert explain_sharpe(1.5).mood == "good"
    assert explain_sharpe(-0.2).mood == "worried"
    _assert_no_jargon(explain_sharpe(1.5).simple)


def test_explain_drawdown_severe_is_worried():
    result = explain_drawdown(-0.35)
    assert result.mood == "worried"
    _assert_no_jargon(result.simple)
    assert "35%" in result.simple


def test_explain_drawdown_fraction_simile_scales_with_severity():
    # The "savings jar" fraction claimed in Simple Mode must not undersell how large
    # the actual drawdown was (e.g. a 51% drop is "about half", not "almost a third").
    assert "third" in explain_drawdown(-0.31).simple
    assert "more than a third" in explain_drawdown(-0.37).simple
    assert "about half" in explain_drawdown(-0.51).simple


def test_explain_diversification_bands():
    assert explain_diversification(80.0).mood == "good"
    assert explain_diversification(30.0).mood == "worried"
    assert explain_diversification(55.0).mood == "neutral"
    assert explain_diversification(None).mood == "neutral"
    _assert_no_jargon(explain_diversification(80.0).simple)


def test_explain_risk_level_bands():
    assert explain_risk_level("Low", 0.10).mood == "good"
    assert explain_risk_level("Medium", 0.20).mood == "neutral"
    assert explain_risk_level("High", 0.40).mood == "worried"
    _assert_no_jargon(explain_risk_level("High", 0.40).simple)


def test_explain_risk_level_handles_missing_volatility():
    result = explain_risk_level("Medium", None)
    assert isinstance(result, Explanation)
    assert result.mood == "neutral"


def test_explain_fundamentals_high_pe_is_neutral_not_worried():
    result = explain_fundamentals(pe_ratio=55.0, dividend_yield=None)
    assert result.mood == "neutral"
    _assert_no_jargon(result.simple)
    assert "55" in result.professional


def test_explain_fundamentals_negative_pe_is_worried():
    result = explain_fundamentals(pe_ratio=-3.0, dividend_yield=None)
    assert result.mood == "worried"
    _assert_no_jargon(result.simple)


def test_explain_fundamentals_includes_dividend_yield_when_present():
    result = explain_fundamentals(pe_ratio=18.0, dividend_yield=0.015)
    assert "1.5%" in result.simple or "1.5%" in result.professional


def test_explain_fundamentals_handles_missing_data():
    result = explain_fundamentals(pe_ratio=None, dividend_yield=None)
    assert isinstance(result, Explanation)
    assert result.mood == "neutral"


def test_explain_sentiment_positive_negative_neutral():
    assert explain_sentiment(0.5).mood == "good"
    assert explain_sentiment(-0.5).mood == "worried"
    assert explain_sentiment(0.0).mood == "neutral"
    assert explain_sentiment(None).mood == "neutral"
    _assert_no_jargon(explain_sentiment(0.5).simple)


def test_explain_ml_prediction_mentions_direction_and_uncertainty():
    result = explain_ml_prediction(predicted_up=True, probability=0.56, historical_accuracy=0.53)
    assert "up" in result.simple.lower()
    assert "guess" in result.simple.lower()
    assert "56" in result.professional or "0.56" in result.professional
    _assert_no_jargon(result.simple)


def test_explain_ml_prediction_down_direction():
    result = explain_ml_prediction(predicted_up=False, probability=0.55, historical_accuracy=0.5)
    assert "down" in result.simple.lower()


def test_explain_ml_prediction_include_probability_false_omits_the_percentage():
    # Regression check for the Chances/Probability feature removal on ML Signals:
    # explicitly requesting include_probability=False must never state the raw
    # probability value, while the default (True, used by "Ask FinSight AI") is
    # unaffected -- see test_explain_ml_prediction_mentions_direction_and_uncertainty.
    result = explain_ml_prediction(predicted_up=True, probability=0.56, historical_accuracy=0.53, include_probability=False)
    assert "56" not in result.professional
    assert "0.56" not in result.professional
    assert "probability" not in result.professional.lower()
    assert "up" in result.professional.lower()
    assert "53" in result.professional or "0.53" in result.professional  # accuracy framing stays


def test_explain_ml_prediction_default_still_includes_probability():
    # The default (used by core.chat's Ask FinSight AI) must remain byte-for-byte
    # unaffected by the ML-Signals-only include_probability=False call site.
    result = explain_ml_prediction(predicted_up=True, probability=0.56, historical_accuracy=0.53)
    assert "56" in result.professional or "0.56" in result.professional


def test_all_none_inputs_never_raise():
    for fn, args in [
        (explain_rsi, (None,)),
        (explain_macd, (None, None)),
        (explain_support, (None, None)),
        (explain_resistance, (None, None)),
        (explain_bollinger, (None, None, None)),
        (explain_volatility, (None,)),
        (explain_atr, (None, None)),
        (explain_adx, (None,)),
        (explain_vwap, (None, None)),
        (explain_sharpe, (None,)),
        (explain_drawdown, (None,)),
        (explain_sentiment, (None,)),
    ]:
        result = fn(*args)
        assert isinstance(result, Explanation)
        assert result.simple
        assert result.professional
