"""News sentiment scoring: Gemini when GEMINI_API_KEY is set, rule-based keyword fallback otherwise."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timezone

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import GEMINI_API_KEY, get_logger
from core.database import NewsSentiment, Ticker, get_session

logger = get_logger(__name__)

MODEL_NAME = "gemini-flash-latest"  # alias that tracks the current flash model, avoiding hardcoded-version staleness

_POSITIVE_WORDS = {
    "beat", "beats", "growth", "profit", "profits", "surge", "surges", "rally", "rallies",
    "gain", "gains", "upgrade", "upgraded", "outperform", "record", "strong", "bullish",
    "expansion", "boost", "boosts", "rise", "rises", "rising", "soar", "soars", "positive",
    "win", "wins", "winning", "success", "successful", "improve", "improves", "improved",
    "high", "higher", "milestone", "partnership", "deal", "approval", "approved",
}
_NEGATIVE_WORDS = {
    "miss", "misses", "missed", "loss", "losses", "decline", "declines", "plunge", "plunges",
    "fall", "falls", "falling", "downgrade", "downgraded", "underperform", "weak", "bearish",
    "cut", "cuts", "layoff", "layoffs", "slump", "slumps", "drop", "drops", "dropping",
    "negative", "fail", "fails", "failure", "lawsuit", "probe", "investigation", "fraud",
    "low", "lower", "risk", "risks", "concern", "concerns", "warning", "recall", "delay",
}


@dataclass
class SentimentResult:
    sentiment: float  # -1..1
    confidence: float  # 0..1
    rationale: str


@dataclass
class NewsArticle:
    headline: str
    summary: str
    published_at: datetime
    source: str
    url: str


def fetch_news(symbol: str, limit: int = 10) -> list[NewsArticle]:
    """Fetch recent news headlines for a symbol via yfinance. Returns [] on failure or if none exist."""
    try:
        raw = yf.Ticker(symbol).news
    except Exception as exc:
        logger.warning("Could not fetch news for %s: %s", symbol, exc)
        return []

    articles: list[NewsArticle] = []
    for item in (raw or [])[:limit]:
        content = item.get("content", {})
        title = content.get("title")
        if not title:
            continue
        pub_date_str = content.get("pubDate")
        try:
            published_at = (
                datetime.fromisoformat(pub_date_str.replace("Z", "+00:00")) if pub_date_str else datetime.now(timezone.utc)
            )
        except ValueError:
            published_at = datetime.now(timezone.utc)
        provider = content.get("provider") or {}
        canonical_url = content.get("canonicalUrl") or {}
        articles.append(
            NewsArticle(
                headline=title,
                summary=content.get("summary") or "",
                published_at=published_at,
                source=provider.get("displayName") or "Unknown",
                url=canonical_url.get("url") or "",
            )
        )
    return articles


def rule_based_sentiment(text: str) -> SentimentResult:
    """Keyword-polarity fallback scorer, used when no Gemini API key is configured or Gemini fails."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    pos = sum(1 for w in words if w in _POSITIVE_WORDS)
    neg = sum(1 for w in words if w in _NEGATIVE_WORDS)
    total_hits = pos + neg

    if total_hits == 0:
        return SentimentResult(sentiment=0.0, confidence=0.2, rationale="No strong sentiment keywords detected (fallback mode).")

    score = (pos - neg) / total_hits
    confidence = min(0.3 + 0.1 * total_hits, 0.7)
    rationale = f"Fallback keyword scoring: {pos} positive vs {neg} negative signal words."
    return SentimentResult(sentiment=round(score, 2), confidence=round(confidence, 2), rationale=rationale)


def gemini_sentiment(text: str) -> SentimentResult:
    """Score sentiment with Gemini. Raises on any failure so the caller can fall back."""
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    prompt = (
        "You are a financial news sentiment analyst. Given the headline and summary below, "
        "respond with ONLY a JSON object of the form "
        '{"sentiment": <float from -1 to 1>, "confidence": <float from 0 to 1>, "rationale": "<one sentence>"}.\n\n'
        f"Text: {text}"
    )
    response = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
    data = json.loads(response.text)
    return SentimentResult(
        sentiment=max(-1.0, min(1.0, float(data["sentiment"]))),
        confidence=max(0.0, min(1.0, float(data["confidence"]))),
        rationale=str(data["rationale"])[:500],
    )


def score_sentiment(text: str) -> tuple[SentimentResult, bool]:
    """Score text with Gemini if a key is configured, else rule-based. Returns (result, used_gemini)."""
    if GEMINI_API_KEY:
        try:
            return gemini_sentiment(text), True
        except Exception as exc:
            logger.warning("Gemini sentiment call failed, falling back to rule-based: %s", exc)
    return rule_based_sentiment(text), False


def analyze_ticker_sentiment(symbol: str, limit: int = 10) -> list[dict]:
    """Fetch recent news for a symbol, score it, and idempotently store new rows. Returns newly stored rows.

    Headlines already stored are skipped *before* scoring (not just before the write),
    so a re-click never re-spends a Gemini call on a headline it's already scored. The
    write itself is a real SQLite UPSERT (INSERT ... ON CONFLICT DO NOTHING on the
    (ticker_id, headline) unique constraint) rather than a bare insert, so even a race
    between two concurrent calls can't produce a duplicate row.
    """
    articles = fetch_news(symbol, limit=limit)
    if not articles:
        return []

    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return []

        existing_headlines = set(
            session.execute(
                select(NewsSentiment.headline).where(NewsSentiment.ticker_id == ticker.id)
            ).scalars().all()
        )

        new_rows: list[dict] = []
        for article in articles:
            if article.headline in existing_headlines:
                continue
            text = f"{article.headline}. {article.summary}".strip()
            result, _used_gemini = score_sentiment(text)
            insert_result = session.execute(
                sqlite_insert(NewsSentiment)
                .values(
                    ticker_id=ticker.id,
                    date=article.published_at.date(),
                    headline=article.headline,
                    sentiment=result.sentiment,
                    confidence=result.confidence,
                    summary=result.rationale,
                    source=article.source,
                )
                .on_conflict_do_nothing(index_elements=["ticker_id", "headline"])
            )
            existing_headlines.add(article.headline)
            if insert_result.rowcount == 0:
                continue  # a concurrent call already inserted this headline
            new_rows.append(
                {
                    "date": article.published_at.date(),
                    "headline": article.headline,
                    "sentiment": result.sentiment,
                    "confidence": result.confidence,
                    "summary": result.rationale,
                    "source": article.source,
                    "url": article.url,
                }
            )
        return new_rows


def get_stored_sentiment(symbol: str) -> list[dict]:
    """All stored sentiment rows for a symbol, most recent first."""
    with get_session() as session:
        ticker = session.execute(select(Ticker).where(Ticker.symbol == symbol.upper())).scalar_one_or_none()
        if ticker is None:
            return []
        rows = session.execute(
            select(NewsSentiment).where(NewsSentiment.ticker_id == ticker.id).order_by(NewsSentiment.date.desc())
        ).scalars().all()
        return [
            {
                "date": r.date,
                "headline": r.headline,
                "sentiment": r.sentiment,
                "confidence": r.confidence,
                "summary": r.summary,
                "source": r.source,
            }
            for r in rows
        ]
