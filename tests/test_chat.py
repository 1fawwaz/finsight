"""Tests for core.chat: intent detection, conversation memory, entity extraction, the
market-calendar-aware context pipeline, the structured rule-based fallback, and
Gemini-with-fallback answering."""

import pytest

from core.chat import (
    INTENT_COMPARISON,
    INTENT_GENERAL,
    INTENT_INDICATOR,
    INTENT_MARKET,
    INTENT_PORTFOLIO,
    INTENT_SECTOR,
    INTENT_SINGLE_STOCK,
    ConversationMemory,
    _build_fallback_answer,
    _fallback_answer,
    _symbol_context,
    answer_question,
    detect_intent,
    extract_symbols,
    gather_context,
    resolve_symbols_with_memory,
)
from core.fundamentals import Fundamentals


# --- extract_symbols --------------------------------------------------------------------


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
    for word in ["is", "are", "how", "risk", "compare", "market", "stock", "explain"]:
        assert extract_symbols(word) == []


# --- Intent detection --------------------------------------------------------------------


def test_detect_intent_portfolio_review():
    assert detect_intent("How is my portfolio doing?", []) == INTENT_PORTFOLIO
    assert detect_intent("Review portfolio", []) == INTENT_PORTFOLIO


def test_detect_intent_market_overview():
    assert detect_intent("How is the market today?", []) == INTENT_MARKET


def test_detect_intent_indicator_explainer():
    assert detect_intent("Explain RSI", []) == INTENT_INDICATOR
    assert detect_intent("What is MACD?", []) == INTENT_INDICATOR


def test_detect_intent_comparison_needs_two_symbols():
    assert detect_intent("Compare TCS vs Infosys", ["TCS.NS", "INFY.NS"]) == INTENT_COMPARISON


def test_detect_intent_single_stock():
    assert detect_intent("Analyze Wipro", ["WIPRO.NS"]) == INTENT_SINGLE_STOCK
    assert detect_intent("Why is Reliance falling?", ["RELIANCE.NS"]) == INTENT_SINGLE_STOCK


def test_detect_intent_sector_query():
    assert detect_intent("Best IT stock?", []) == INTENT_SECTOR
    assert detect_intent("Which bank stock should I buy?", []) == INTENT_SECTOR


def test_detect_intent_general_fallback():
    assert detect_intent("What's up?", []) == INTENT_GENERAL


def test_detect_intent_sector_keyword_requires_word_boundary():
    # Regression: a naive substring check on the 2-letter "it" sector keyword would
    # false-positive on any question containing "it" inside another word (e.g. "with").
    assert detect_intent("What is it worth?", []) != INTENT_SECTOR


# --- Conversation memory / follow-up resolution -------------------------------------------


def test_resolve_symbols_with_memory_no_memory_uses_extracted():
    symbols, used_memory = resolve_symbols_with_memory("Analyze Wipro", ConversationMemory())
    assert symbols == ["WIPRO.NS"]
    assert used_memory is False


def test_resolve_symbols_with_memory_pure_followup_inherits_everything():
    memory = ConversationMemory(symbols=["TCS.NS", "INFY.NS"], last_intent=INTENT_COMPARISON)
    symbols, used_memory = resolve_symbols_with_memory("Which one is safer?", memory)
    assert symbols == ["TCS.NS", "INFY.NS"]
    assert used_memory is True


def test_resolve_symbols_with_memory_what_about_adds_to_prior_context():
    memory = ConversationMemory(symbols=["TCS.NS"], last_intent=INTENT_SINGLE_STOCK)
    symbols, used_memory = resolve_symbols_with_memory("What about Infosys?", memory)
    assert symbols == ["TCS.NS", "INFY.NS"]
    assert used_memory is True


def test_resolve_symbols_with_memory_fresh_question_ignores_memory():
    memory = ConversationMemory(symbols=["TCS.NS"], last_intent=INTENT_SINGLE_STOCK)
    symbols, used_memory = resolve_symbols_with_memory("Analyze Wipro", memory)
    assert symbols == ["WIPRO.NS"]
    assert used_memory is False


def test_answer_question_updates_conversation_memory(monkeypatch):
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "")
    _, _, memory = answer_question("Analyze Wipro", "Simple", ConversationMemory())
    assert memory.symbols == ["WIPRO.NS"]
    assert memory.last_intent == INTENT_SINGLE_STOCK


def test_answer_question_followup_uses_prior_memory(monkeypatch):
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "")
    _, _, memory_after_first = answer_question("Analyze TCS", "Simple", ConversationMemory())
    answer, _, memory_after_second = answer_question("What about Infosys?", "Simple", memory_after_first)
    assert "TCS" in answer
    assert "INFY" in answer or "Infosys" in answer
    assert set(memory_after_second.symbols) == {"TCS.NS", "INFY.NS"}


# --- gather_context ------------------------------------------------------------------------


def test_symbol_context_computes_dividend_yield_from_rate_and_price(temp_db, monkeypatch):
    # Regression: yfinance's own `dividendYield` field disagreed with reality for some
    # tickers, so the dividend yield shown to the user must be computed here from a
    # verified dividend rate (rupees/share) and the stock's own current price, not
    # trusted from that field.
    from datetime import date

    from core.database import Price, Ticker, get_session

    with get_session() as session:
        ticker = Ticker(symbol="WIPRO.NS", name="Wipro Limited", sector="Technology")
        session.add(ticker)
        session.flush()
        session.add(
            Price(ticker_id=ticker.id, date=date(2026, 7, 9), open=169.0, high=174.0, low=168.0, close=172.0, volume=1_000_000)
        )
        session.add(
            Price(ticker_id=ticker.id, date=date(2026, 7, 10), open=170.0, high=176.0, low=169.0, close=175.46, volume=1_000_000)
        )

    monkeypatch.setattr(
        "core.chat.get_fundamentals",
        lambda symbol: Fundamentals(
            market_cap=None, pe_ratio=14.0, dividend_rate=11.0, fifty_two_week_high=None, fifty_two_week_low=None, available=True
        ),
    )

    context = _symbol_context("WIPRO.NS")
    assert context["dividend_yield"] == pytest.approx(11.0 / 175.46, rel=1e-3)


def test_gather_context_never_embeds_ns_or_bo_suffix():
    # gather_context's return value is embedded verbatim into the Gemini prompt, and an
    # LLM will readily echo back whatever's in its context -- so no key or value here
    # may contain a `.NS`/`.BO` suffix, or it could leak into a Gemini-generated answer
    # shown to the user (the one thing the spec says must never happen).
    context = gather_context("Should I buy TCS?")
    serialized = str(context)
    assert ".NS" not in serialized
    assert ".BO" not in serialized


def test_gather_context_always_includes_calendar():
    context = gather_context("How is the market today?")
    calendar = context["calendar"]
    assert calendar["current_date"]
    assert calendar["current_time_ist"]
    assert calendar["market_status"]
    assert calendar["next_trading_session"]


def test_gather_context_includes_intent():
    context = gather_context("Explain RSI")
    assert context["intent"] == INTENT_INDICATOR


def test_gather_context_sector_query_includes_candidates_key():
    context = gather_context("Best IT stock?")
    assert context["intent"] == INTENT_SECTOR
    assert "sector_query" in context
    assert context["sector_query"] == "Technology"
    assert "sector_candidates" in context


# --- Structured fallback -----------------------------------------------------------------


def test_fallback_answer_without_symbols_is_helpful_not_blank():
    text = _fallback_answer("What's up?", {"mentioned_symbols": [], "intent": INTENT_GENERAL, "calendar": {}})
    assert text
    assert "couldn't find" in text.lower()


def test_fallback_single_stock_includes_price_and_never_shows_ns_suffix():
    context = {
        "mentioned_symbols": ["TCS"],
        "intent": INTENT_SINGLE_STOCK,
        "calendar": {},
        "TCS": {"available": True, "last_close": 3500.0, "rsi_14": 55.0},
    }
    text = _build_fallback_answer("How is TCS doing?", context, "Simple")
    assert "TCS" in text
    assert "3,500.00" in text
    assert ".NS" not in text
    assert ".BO" not in text


def test_fallback_includes_calendar_header_when_present():
    context = {
        "mentioned_symbols": [],
        "intent": INTENT_MARKET,
        "calendar": {"current_date": "Sunday, 12 July 2026", "current_time_ist": "20:00 IST", "market_status": "Market Closed — Weekend"},
        "market": {},
    }
    text = _build_fallback_answer("How is the market today?", context, "Simple")
    assert "Sunday, 12 July 2026" in text
    assert "Market Closed" in text


def test_fallback_portfolio_lists_holdings():
    context = {
        "intent": INTENT_PORTFOLIO,
        "calendar": {},
        "portfolios": [{"name": "Core", "holdings": [{"symbol": "TCS", "shares": 5, "avg_cost": 3000}]}],
    }
    text = _build_fallback_answer("Review my portfolio", context, "Simple")
    assert "Core" in text
    assert "TCS" in text


def test_fallback_portfolio_handles_no_portfolios():
    context = {"intent": INTENT_PORTFOLIO, "calendar": {}, "portfolios": []}
    text = _build_fallback_answer("Review my portfolio", context, "Simple")
    assert "don't have any portfolios" in text.lower()


def test_fallback_market_uses_real_index_numbers():
    context = {
        "intent": INTENT_MARKET,
        "calendar": {},
        "market": {"nifty_50": {"1d_change_pct": 0.012}, "sensex": {"1d_change_pct": 0.009}},
    }
    text = _build_fallback_answer("How is the market today?", context, "Simple")
    assert "Nifty 50" in text
    assert "1.2%" in text


def test_fallback_indicator_explains_rsi_generically():
    context = {"intent": INTENT_INDICATOR, "calendar": {}, "mentioned_symbols": []}
    text = _build_fallback_answer("Explain RSI", context, "Simple")
    assert "speedometer" in text.lower() or "0 to 100" in text.lower()


def test_fallback_indicator_enriches_with_live_value_when_symbol_known():
    context = {
        "intent": INTENT_INDICATOR,
        "calendar": {},
        "mentioned_symbols": ["TCS"],
        "TCS": {"available": True, "rsi_14": 62.0},
    }
    text = _build_fallback_answer("What's the RSI of TCS?", context, "Professional")
    assert "62.0" in text


def test_fallback_comparison_includes_side_by_side_line():
    context = {
        "intent": INTENT_COMPARISON,
        "calendar": {},
        "mentioned_symbols": ["TCS", "INFY"],
        "TCS": {"available": True, "last_close": 3500.0, "rsi_14": 55.0},
        "INFY": {"available": True, "last_close": 1500.0, "rsi_14": 45.0},
    }
    text = _build_fallback_answer("Compare TCS vs Infosys", context, "Simple")
    assert "Side by side" in text
    assert "TCS" in text and "INFY" in text


def test_fallback_sector_frames_as_informational_not_a_ranking():
    context = {
        "intent": INTENT_SECTOR,
        "calendar": {},
        "sector_query": "Technology",
        "sector_candidates": [{"symbol": "TCS", "last_close": 3500.0}],
    }
    text = _build_fallback_answer("Best IT stock?", context, "Simple")
    assert "not" in text.lower()
    assert "TCS" in text


def test_fallback_sector_handles_no_tracked_candidates():
    context = {"intent": INTENT_SECTOR, "calendar": {}, "sector_query": "Technology", "sector_candidates": []}
    text = _build_fallback_answer("Best IT stock?", context, "Simple")
    assert "don't have enough tracked" in text.lower()


# --- answer_question (Gemini + fallback) --------------------------------------------------


def test_answer_question_falls_back_without_api_key(monkeypatch):
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "")
    answer, used_gemini, _memory = answer_question("Should I buy TCS?", "Simple")

    assert used_gemini is False
    assert answer


def test_answer_question_falls_back_when_gemini_raises(monkeypatch):
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.chat._gemini_answer",
        lambda question, context, mode: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    answer, used_gemini, _memory = answer_question("Should I buy TCS?", "Professional")

    assert used_gemini is False
    assert answer


def test_answer_question_never_says_tomorrow(monkeypatch):
    # Regression: prediction language must always use the real next-trading-session
    # date, never a hardcoded "tomorrow" that could be a weekend or exchange holiday.
    monkeypatch.setattr("core.chat.GEMINI_API_KEY", "")
    answer, _, _ = answer_question("Predict Wipro tomorrow", "Simple")
    assert "tomorrow" not in answer.lower()
