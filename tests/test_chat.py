"""Tests for core.chat: entity extraction from free-text questions and Gemini-with-fallback answering."""

import pytest

from core.chat import _fallback_answer, answer_question, extract_symbols


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Should I buy TCS?", ["TCS.NS"]),
        ("Explain Reliance like I am a beginner", ["RELIANCE.NS"]),
        ("Why is Infosys falling today?", ["INFY.NS"]),
        ("How is HDFC Bank doing?", ["HDFCBANK.NS"]),
    ],
)
def test_extract_symbols_finds_expected_ticker(question, expected):
    assert extract_symbols(question) == expected


def test_extract_symbols_handles_multiple_companies_in_order():
    result = extract_symbols("Compare TCS vs Infosys")
    assert result == ["TCS.NS", "INFY.NS"]


def test_extract_symbols_multiword_company_name():
    result = extract_symbols("What about Wipro and Tata Motors?")
    assert "WIPRO.NS" in result
    assert "TMCV.NS" in result


def test_extract_symbols_returns_empty_for_generic_questions():
    assert extract_symbols("Which stock is less risky?") == []
    assert extract_symbols("Summarize today market in under 30 seconds") == []
    assert extract_symbols("How is my portfolio doing?") == []


def test_extract_symbols_never_matches_common_question_words():
    # Regression: short common words match real NSE symbol *prefixes* (e.g. "is" ->
    # ISFT.NS), which is correct for the live search boxes but must never leak into
    # chat entity extraction.
    for word in ["is", "are", "how", "risk", "compare", "market", "stock", "explain"]:
        assert extract_symbols(word) == []


def test_fallback_answer_without_symbols_is_helpful_not_blank():
    text = _fallback_answer("What's up?", {"mentioned_symbols": []})
    assert text
    assert "couldn't find" in text.lower()


def test_fallback_answer_with_symbol_data_summarizes_it():
    context = {
        "mentioned_symbols": ["TCS.NS"],
        "TCS.NS": {"available": True, "last_close": 3500.0, "rsi_14": 55.0},
    }
    text = _fallback_answer("How is TCS doing?", context)
    assert "TCS" in text
    assert "3500.0" in text


def test_answer_question_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "")
    answer, used_gemini = answer_question("Should I buy TCS?", "Simple")

    assert used_gemini is False
    assert answer


def test_answer_question_falls_back_when_gemini_raises(monkeypatch):
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.chat._gemini_answer",
        lambda question, context, mode: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    answer, used_gemini = answer_question("Should I buy TCS?", "Professional")

    assert used_gemini is False
    assert answer
