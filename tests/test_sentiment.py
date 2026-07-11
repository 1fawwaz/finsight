"""Tests for core.sentiment: fallback scoring, Gemini fallback-on-error, news parsing, idempotent storage."""

import pytest
from sqlalchemy import select

from core.database import Ticker, get_session
from core.sentiment import (
    NewsArticle,
    SentimentResult,
    analyze_ticker_sentiment,
    fetch_news,
    get_stored_sentiment,
    rule_based_sentiment,
    score_sentiment,
)
from datetime import datetime, timezone


def test_rule_based_sentiment_positive():
    result = rule_based_sentiment("Company beats estimates, profit surges to record high")
    assert result.sentiment > 0
    assert 0 <= result.confidence <= 1


def test_rule_based_sentiment_negative():
    result = rule_based_sentiment("Company misses estimates, layoffs and lawsuit warning")
    assert result.sentiment < 0


def test_rule_based_sentiment_neutral_when_no_keywords():
    result = rule_based_sentiment("The company held its quarterly meeting on Tuesday")
    assert result.sentiment == 0.0
    assert result.confidence == 0.2
    assert "fallback mode" in result.rationale.lower()


def test_score_sentiment_uses_rule_based_when_no_api_key(monkeypatch):
    monkeypatch.setattr("core.sentiment.GEMINI_API_KEY", "")
    result, used_gemini = score_sentiment("profit surges to record high")
    assert used_gemini is False
    assert result.sentiment > 0


def test_score_sentiment_falls_back_when_gemini_raises(monkeypatch):
    monkeypatch.setattr("core.sentiment.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.sentiment.gemini_sentiment",
        lambda text: (_ for _ in ()).throw(RuntimeError("API error")),
    )
    result, used_gemini = score_sentiment("profit surges to record high")
    assert used_gemini is False
    assert result.sentiment > 0


def test_score_sentiment_uses_gemini_when_available(monkeypatch):
    monkeypatch.setattr("core.sentiment.GEMINI_API_KEY", "fake-key")
    monkeypatch.setattr(
        "core.sentiment.gemini_sentiment",
        lambda text: SentimentResult(sentiment=0.8, confidence=0.9, rationale="Strong positive news."),
    )
    result, used_gemini = score_sentiment("some text")
    assert used_gemini is True
    assert result.sentiment == 0.8


def test_fetch_news_parses_yfinance_shape(monkeypatch):
    fixture = [
        {
            "content": {
                "title": "Company announces record profit",
                "summary": "Strong quarter overall.",
                "pubDate": "2026-06-22T05:09:16Z",
                "provider": {"displayName": "Example News"},
                "canonicalUrl": {"url": "https://example.com/article"},
            }
        },
        {"content": {"title": None}},  # should be skipped (no title)
    ]
    monkeypatch.setattr(
        "core.sentiment.yf.Ticker",
        lambda symbol: type("T", (), {"news": fixture})(),
    )
    articles = fetch_news("RELIANCE.NS")
    assert len(articles) == 1
    article = articles[0]
    assert article.headline == "Company announces record profit"
    assert article.source == "Example News"
    assert article.url == "https://example.com/article"
    assert article.published_at.tzinfo is not None


def test_fetch_news_returns_empty_on_error(monkeypatch):
    def _raise(symbol):
        raise RuntimeError("network error")

    monkeypatch.setattr("core.sentiment.yf.Ticker", _raise)
    assert fetch_news("RELIANCE.NS") == []


def test_analyze_ticker_sentiment_is_idempotent(temp_db, monkeypatch):
    with get_session() as session:
        session.add(Ticker(symbol="RELIANCE.NS", name="Reliance Industries Ltd.", sector="Energy"))

    fixed_articles = [
        NewsArticle(
            headline="Company beats estimates",
            summary="Profit surges",
            published_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
            source="Example News",
            url="https://example.com/a",
        )
    ]
    monkeypatch.setattr("core.sentiment.fetch_news", lambda symbol, limit=10: fixed_articles)
    monkeypatch.setattr("core.sentiment.GEMINI_API_KEY", "")

    first_run = analyze_ticker_sentiment("RELIANCE.NS")
    second_run = analyze_ticker_sentiment("RELIANCE.NS")

    assert len(first_run) == 1
    assert len(second_run) == 0  # already stored, no duplicates

    stored = get_stored_sentiment("RELIANCE.NS")
    assert len(stored) == 1
    assert stored[0]["headline"] == "Company beats estimates"


def test_analyze_ticker_sentiment_unknown_symbol_returns_empty(temp_db, monkeypatch):
    monkeypatch.setattr("core.sentiment.fetch_news", lambda symbol, limit=10: [
        NewsArticle("H", "S", datetime.now(timezone.utc), "Src", "https://x")
    ])
    assert analyze_ticker_sentiment("NOPE.NS") == []


def test_analyze_ticker_sentiment_upsert_survives_a_race(temp_db, monkeypatch):
    """A genuine race: a row for this exact (ticker, headline) gets inserted by another
    caller *after* the in-memory pre-check but *before* this call's own insert. The DB-level
    ON CONFLICT DO NOTHING must absorb that without raising IntegrityError or duplicating."""
    with get_session() as session:
        session.add(Ticker(symbol="RELIANCE.NS", name="Reliance Industries Ltd.", sector="Energy"))

    article = NewsArticle(
        headline="Company beats estimates",
        summary="Profit surges",
        published_at=datetime(2026, 6, 22, tzinfo=timezone.utc),
        source="Example News",
        url="https://example.com/a",
    )
    monkeypatch.setattr("core.sentiment.fetch_news", lambda symbol, limit=10: [article])
    monkeypatch.setattr("core.sentiment.GEMINI_API_KEY", "")

    from core.database import NewsSentiment

    def _score_then_simulate_concurrent_insert(text):
        # Simulate another process/session inserting the same (ticker, headline) row
        # in between this call's pre-check and its own insert.
        with get_session() as session:
            ticker = session.execute(select(Ticker).where(Ticker.symbol == "RELIANCE.NS")).scalar_one()
            session.add(
                NewsSentiment(
                    ticker_id=ticker.id,
                    date=article.published_at.date(),
                    headline=article.headline,
                    sentiment=0.5,
                    confidence=0.5,
                    summary="inserted concurrently",
                    source="Other",
                )
            )
        return rule_based_sentiment(text)

    monkeypatch.setattr("core.sentiment.score_sentiment", lambda text: (_score_then_simulate_concurrent_insert(text), False))

    new_rows = analyze_ticker_sentiment("RELIANCE.NS")  # must not raise IntegrityError

    assert new_rows == []  # the concurrent insert won the race; this call found 0 new rows
    stored = get_stored_sentiment("RELIANCE.NS")
    assert len(stored) == 1
    assert stored[0]["summary"] == "inserted concurrently"
