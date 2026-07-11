"""Tests for core.ai_explain: Gemini-with-fallback AI panel generation."""

from core.ai_explain import generate_ai_panel


def test_generate_ai_panel_uses_fallback_without_api_key(monkeypatch):
    monkeypatch.setattr("core.ai_explain.GEMINI_API_KEY", "")

    text, used_gemini = generate_ai_panel("Stock Analysis for RELIANCE.NS", {"rsi": 55}, "fallback summary", "Simple")

    assert used_gemini is False
    assert text == "fallback summary"


def test_generate_ai_panel_falls_back_when_gemini_raises(monkeypatch):
    monkeypatch.setattr("core.ai_explain.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.ai_explain._gemini_panel_for_mode",
        lambda context_label, data, mode: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    text, used_gemini = generate_ai_panel("Portfolio review", {"sharpe": 1.2}, "fallback summary", "Professional")

    assert used_gemini is False
    assert text == "fallback summary"


def test_generate_ai_panel_uses_gemini_when_available(monkeypatch):
    monkeypatch.setattr("core.ai_explain.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.ai_explain._gemini_panel_for_mode",
        lambda context_label, data, mode: "a real gemini narrative",
    )

    text, used_gemini = generate_ai_panel("ML Signals for TCS.NS", {"probability_up": 0.55}, "fallback summary", "Simple")

    assert used_gemini is True
    assert text == "a real gemini narrative"


def test_generate_ai_panel_never_returns_blank():
    text, _ = generate_ai_panel("context", {}, "fallback summary", "Simple")
    assert text
