"""AI-narrated "today's market" summary for the home dashboard.

Same Gemini-with-rule-based-fallback pattern as core.sentiment: grounded only in real
numbers the app already computed (never invented), and the fallback must never be blank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.config import GEMINI_API_KEY, get_logger

logger = get_logger(__name__)

MODEL_NAME = "gemini-flash-latest"


@dataclass
class MarketSnapshot:
    """Real numbers the summary must be grounded in -- nothing here is invented."""

    nifty_pct: Optional[float] = None
    sensex_pct: Optional[float] = None
    banknifty_pct: Optional[float] = None
    top_gainer: Optional[tuple[str, float]] = None  # (symbol, 1D % as a fraction)
    top_loser: Optional[tuple[str, float]] = None


def _direction_word(pct: float) -> str:
    return "up" if pct >= 0 else "down"


def rule_based_summary(snapshot: MarketSnapshot) -> str:
    """Deterministic, always-available summary built directly from the snapshot numbers."""
    index_bits = []
    if snapshot.nifty_pct is not None:
        index_bits.append(f"Nifty 50 is {_direction_word(snapshot.nifty_pct)} {abs(snapshot.nifty_pct):.1%} today")
    if snapshot.sensex_pct is not None:
        index_bits.append(f"Sensex is {_direction_word(snapshot.sensex_pct)} {abs(snapshot.sensex_pct):.1%}")
    if snapshot.banknifty_pct is not None:
        index_bits.append(f"Bank Nifty is {_direction_word(snapshot.banknifty_pct)} {abs(snapshot.banknifty_pct):.1%}")

    summary = (", ".join(index_bits) + "." ) if index_bits else "Market index data isn't available right now."

    mover_bits = []
    if snapshot.top_gainer is not None:
        symbol, pct = snapshot.top_gainer
        mover_bits.append(f"{symbol} leads your watchlist, up {pct:.1%}")
    if snapshot.top_loser is not None:
        symbol, pct = snapshot.top_loser
        if pct < 0:
            mover_bits.append(f"{symbol} is your weakest, down {abs(pct):.1%}")
    if mover_bits:
        summary += " " + "; ".join(mover_bits) + "."

    return summary


def _gemini_summary(snapshot: MarketSnapshot) -> str:
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    prompt = (
        "You are a calm, factual summarizer of the Indian stock market. Using ONLY the "
        "numbers given below (never invent any number not listed), write a plain-English "
        "summary of today's market in 2 short sentences, under 40 words total. If a number "
        "is missing, simply don't mention it.\n\n"
        f"Nifty 50 change: {snapshot.nifty_pct}\n"
        f"Sensex change: {snapshot.sensex_pct}\n"
        f"Bank Nifty change: {snapshot.banknifty_pct}\n"
        f"Top watchlist gainer (symbol, change): {snapshot.top_gainer}\n"
        f"Top watchlist loser (symbol, change): {snapshot.top_loser}\n"
    )
    response = model.generate_content(prompt)
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned an empty market summary")
    return text


def summarize_market(snapshot: MarketSnapshot) -> tuple[str, bool]:
    """Returns (summary_text, used_gemini). Always returns a non-empty summary."""
    if GEMINI_API_KEY:
        try:
            return _gemini_summary(snapshot), True
        except Exception as exc:  # Gemini errors must never break the home page
            logger.warning("Gemini market summary failed, falling back to rule-based: %s", exc)
    return rule_based_summary(snapshot), False
