""""Ask FinSight AI" chat: answers grounded in the app's own DB data (prices, indicators,
portfolio metrics, news sentiment, ML outputs), not generic LLM knowledge. Gemini
synthesizes the grounding data into an answer; falls back to a data-dump summary if
Gemini is unavailable or fails.
"""

from __future__ import annotations

import re

from core.config import BENCHMARK_NIFTY50, BENCHMARK_SENSEX, GEMINI_API_KEY, get_logger
from core.indicators import macd, rsi, volatility
from core.ml_model import predict_next_direction
from core.portfolio import list_holdings, list_portfolios
from core.queries import get_price_history
from core.sentiment import get_stored_sentiment
from core.universe import display_symbol, resolve_symbol

logger = get_logger(__name__)

MODEL_NAME = "gemini-flash-latest"

# Common English/finance-question vocabulary that must never be treated as a candidate
# ticker/company name -- confirmed empirically that short common words otherwise match
# real NSE symbol *prefixes* (e.g. "is" -> ISFT.NS, "risk" -> ... ), which is correct,
# desirable behavior for the live search boxes but wrong for extracting entities from a
# free-text question.
_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "am",
    "i", "me", "my", "you", "your", "it", "its", "we", "our", "they", "their",
    "he", "she", "him", "her", "this", "that", "these", "those", "what", "which",
    "who", "whom", "why", "how", "when", "where", "and", "or", "but", "if", "so",
    "of", "in", "on", "at", "for", "to", "from", "with", "about", "as", "by",
    "should", "would", "could", "can", "will", "shall", "do", "does", "did",
    "buy", "sell", "hold", "invest", "investing", "investment", "trade", "trading",
    "stock", "stocks", "share", "shares", "equity", "equities", "market", "markets",
    "price", "prices", "priced", "pricing", "value", "worth",
    "risk", "risky", "riskier", "riskiest", "safe", "safer", "safest",
    "good", "bad", "better", "worse", "best", "worst", "strong", "weak",
    "compare", "comparison", "versus", "vs", "against", "between",
    "summarize", "summary", "explain", "beginner", "beginners", "simple", "simply",
    "today", "yesterday", "tomorrow", "now", "currently", "recently", "lately",
    "falling", "fall", "falls", "rising", "rise", "rises", "dropping", "drop", "drops",
    "gaining", "gain", "gains", "losing", "lose", "loses", "up", "down",
    "less", "more", "most", "least", "than", "then",
    "seconds", "minutes", "under", "over", "one", "two", "three",
    "not", "no", "yes", "please", "tell", "give", "show", "like",
    "doing", "going", "looking", "performing", "perform", "performance",
    "let", "know", "think", "guess", "opinion", "thoughts", "want", "need",
    "any", "some", "all", "each", "every", "just", "really", "very", "much",
    "portfolio", "holding", "holdings", "position", "positions",
}


def extract_symbols(question: str, max_symbols: int = 3) -> list[str]:
    """Best-effort extraction of NSE symbols mentioned in a free-text question.

    Tries 3-word, then 2-word, then 1-word candidate phrases (so multi-word company
    names like "HDFC Bank" resolve as a unit before their individual words are tried),
    skipping any candidate that's entirely stopwords or too short to be a safe guess.
    """
    words = re.findall(r"[A-Za-z][A-Za-z&]*", question)
    used_positions: set[int] = set()
    found: list[str] = []
    seen: set[str] = set()

    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            if any(pos in used_positions for pos in range(i, i + size)):
                continue
            phrase_words = words[i : i + size]
            # A real company-name n-gram never contains a filler/question word, so any
            # stopword in the phrase disqualifies it -- this is what keeps phrases like
            # "Explain Reliance like" (real word + stopwords) from being handed whole to
            # fuzzy matching, which was producing garbage matches (e.g. RPOWER instead
            # of RELIANCE) before this was `any()` instead of the looser `all()`.
            if any(w.lower() in _STOPWORDS for w in phrase_words):
                continue
            if size == 1 and len(phrase_words[0]) < 3:
                continue
            phrase = " ".join(phrase_words)
            symbol = resolve_symbol(phrase)
            if symbol and symbol not in seen:
                found.append(symbol)
                seen.add(symbol)
                used_positions.update(range(i, i + size))
                if len(found) >= max_symbols:
                    return found
    return found


def _symbol_context(symbol: str) -> dict:
    """Grounding data for one symbol: latest price/indicators, sentiment, ML view.

    `symbol` (the canonical `.NS`/`.BO` form) is used for the actual DB/network lookups,
    but never appears in the returned dict -- this dict gets embedded verbatim into
    Gemini's prompt text, and an LLM will readily echo back whatever's in its context,
    so the suffix has to be stripped here rather than trusted to be stripped later.
    """
    history = get_price_history(symbol)
    if history.empty:
        return {"symbol": display_symbol(symbol), "available": False}

    close = history["close"]
    context: dict = {
        "symbol": display_symbol(symbol),
        "available": True,
        "last_close": round(float(close.iloc[-1]), 2),
        "1d_change_pct": round(float(close.iloc[-1] / close.iloc[-2] - 1), 4) if len(close) > 1 else None,
        "rsi_14": round(float(rsi(close, window=14).iloc[-1]), 1) if len(close) >= 15 else None,
    }
    macd_df = macd(close)
    if len(macd_df.dropna()) > 0:
        latest_macd = macd_df.dropna().iloc[-1]
        context["macd"] = round(float(latest_macd["macd"]), 2)
        context["macd_signal"] = round(float(latest_macd["signal"]), 2)
    vol = volatility(close, window=20, annualize=True).dropna()
    if len(vol) > 0:
        context["volatility_annualized"] = round(float(vol.iloc[-1]), 3)

    sentiment_rows = get_stored_sentiment(symbol)
    if sentiment_rows:
        scores = [r["sentiment"] for r in sentiment_rows]
        context["mean_sentiment"] = round(sum(scores) / len(scores), 2)
        context["sentiment_article_count"] = len(scores)

    prediction = predict_next_direction(history)
    if prediction is not None:
        predicted_up, probability_up = prediction
        context["ml_next_day_direction"] = "up" if predicted_up else "down"
        context["ml_probability_up"] = round(float(probability_up), 3)

    return context


def _portfolio_context() -> dict:
    """Grounding data for all portfolios: holdings and weights, for "which is riskier"
    or "how is my portfolio doing" style questions."""
    portfolios = list_portfolios()
    result = []
    for p in portfolios:
        holdings = list_holdings(p["id"])
        result.append(
            {
                "name": p["name"],
                "holdings": [
                    {"symbol": display_symbol(h["symbol"]), "shares": h["shares"], "avg_cost": h["avg_cost"]}
                    for h in holdings
                ],
            }
        )
    return {"portfolios": result} if result else {}


def _market_context() -> dict:
    context = {}
    for label, symbol in (("nifty_50", BENCHMARK_NIFTY50), ("sensex", BENCHMARK_SENSEX)):
        history = get_price_history(symbol)
        if not history.empty and len(history) > 1:
            close = history["close"]
            context[label] = {
                "last_close": round(float(close.iloc[-1]), 2),
                "1d_change_pct": round(float(close.iloc[-1] / close.iloc[-2] - 1), 4),
            }
    return context


def gather_context(question: str) -> dict:
    """Assemble grounding data for a question: per-symbol data for any tickers
    mentioned, plus portfolio and market context (cheap, always useful for comparisons
    and "how's the market" questions).

    Keyed by display symbol (no `.NS`/`.BO`) throughout, not the canonical form used
    internally for lookups -- this dict is what gets embedded in the Gemini prompt.
    """
    symbols = extract_symbols(question)
    context: dict = {"mentioned_symbols": [display_symbol(s) for s in symbols]}
    for symbol in symbols:
        context[display_symbol(symbol)] = _symbol_context(symbol)
    context["market"] = _market_context()
    portfolio_context = _portfolio_context()
    if portfolio_context:
        context.update(portfolio_context)
    return context


def _fallback_answer(question: str, context: dict) -> str:
    """A plain data-dump answer used when Gemini is unavailable or fails -- never blank."""
    symbols = context.get("mentioned_symbols", [])
    if not symbols:
        return (
            "I couldn't find a specific stock in your question, and AI narration isn't "
            "available right now. Try naming a company (e.g. \"Reliance\" or \"TCS\"), or "
            "browse the Market Overview / Stock Analysis pages directly."
        )
    lines = ["Here's what I found (AI narration isn't available right now, so this is the raw data):"]
    for symbol in symbols:
        data = context.get(symbol, {})
        if not data.get("available"):
            lines.append(f"- {symbol}: no price data yet.")
            continue
        bits = [f"last close ₹{data['last_close']}"]
        if data.get("rsi_14") is not None:
            bits.append(f"RSI {data['rsi_14']}")
        if data.get("mean_sentiment") is not None:
            bits.append(f"avg sentiment {data['mean_sentiment']:+.2f}")
        if data.get("ml_next_day_direction"):
            bits.append(f"model leans {data['ml_next_day_direction']} ({data['ml_probability_up']:.0%} confidence)")
        lines.append(f"- {symbol}: " + ", ".join(bits))
    return "\n".join(lines)


def _gemini_answer(question: str, context: dict, mode: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    style = (
        "Answer like the user is 10 years old with no finance background -- no jargon at "
        "all, use everyday analogies, short sentences."
        if mode == "Simple"
        else "Answer like a knowledgeable analyst talking to another professional -- "
        "technical language is fine, be specific about numbers and confidence."
    )
    prompt = (
        "You are FinSight's AI assistant for Indian equities (NSE/BSE). Answer the "
        "user's question using ONLY the real data below -- never invent a number, price, "
        "or fact not present here. If the data needed to answer isn't present, say so "
        "honestly instead of guessing.\n\n"
        f"User question: {question}\n\n"
        f"Available data: {context}\n\n"
        f"{style} Keep it under 100 words. Never give direct buy/sell orders -- frame "
        "everything as informational, and end with a brief reminder this isn't financial advice."
    )
    response = model.generate_content(prompt)
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned an empty chat answer")
    return text


def answer_question(question: str, mode: str) -> tuple[str, bool]:
    """Returns (answer, used_gemini). Always returns a non-empty, data-grounded answer."""
    context = gather_context(question)
    fallback = _fallback_answer(question, context)
    if GEMINI_API_KEY:
        try:
            return _gemini_answer(question, context, mode), True
        except Exception as exc:  # Gemini errors must never break the chat
            logger.warning("Gemini chat answer failed, falling back to data dump: %s", exc)
    return fallback, False
