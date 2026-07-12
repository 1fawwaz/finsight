""""Ask FinSight AI" chat: routes every question through a real pipeline -- intent
detection, company resolution (with conversation-memory follow-ups), market-calendar
awareness, live price/technical/fundamental data, news sentiment, ML prediction, and
portfolio context -- before Gemini ever sees it. Gemini narrates that data into an
answer; a structured, section-by-section fallback (built from the same real data via
core.explain) is used if Gemini is unavailable or fails, so the answer is never blank
and never generic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core.config import BENCHMARK_BANKNIFTY, BENCHMARK_NIFTY50, BENCHMARK_SENSEX, GEMINI_API_KEY, GEMINI_TIMEOUT_SECONDS, get_logger
from core.explain import explain_fundamentals, explain_macd, explain_ml_prediction, explain_rsi, explain_sentiment, explain_support, explain_resistance
from core.fundamentals import get_fundamentals
from core.indicators import macd, rsi, support_resistance, volatility
from core.market_status import get_nse_market_status, prediction_target_session
from core.market_summary import MarketSnapshot, rule_based_summary
from core.ml_model import predict_next_direction
from core.portfolio import list_holdings, list_portfolios
from core.queries import get_price_history, get_ticker_info, list_ticker_symbols
from core.sentiment import get_stored_sentiment
from core.universe import display_symbol, resolve_symbol

logger = get_logger(__name__)

MODEL_NAME = "gemini-flash-latest"

# --- Intents -------------------------------------------------------------------------

INTENT_PORTFOLIO = "portfolio_review"
INTENT_MARKET = "market_overview"
INTENT_INDICATOR = "indicator_explainer"
INTENT_SECTOR = "sector_query"
INTENT_COMPARISON = "comparison"
INTENT_SINGLE_STOCK = "single_stock"
INTENT_GENERAL = "general"

_PORTFOLIO_TERMS = {"portfolio", "my holdings", "my holding", "my stocks", "my investments"}
_MARKET_TERMS = {"market", "nifty", "sensex", "bank nifty", "index", "indices"}
_INDICATOR_TERMS = {
    "rsi", "macd", "atr", "adx", "vwap", "bollinger", "sharpe", "drawdown",
    "support", "resistance", "volatility",
}
_RECOMMENDATION_TERMS = {"best", "top", "which stock", "recommend", "should i buy"}
_SECTOR_KEYWORDS: dict[str, str] = {
    "it": "Technology",
    "tech": "Technology",
    "technology": "Technology",
    "bank": "Financial Services",
    "banking": "Financial Services",
    "financial": "Financial Services",
    "pharma": "Healthcare",
    "pharmaceutical": "Healthcare",
    "healthcare": "Healthcare",
    "auto": "Consumer Cyclical",
    "automobile": "Consumer Cyclical",
    "energy": "Energy",
    "oil": "Energy",
    "fmcg": "Consumer Defensive",
}


def _contains_any_term(text: str, terms: set[str]) -> bool:
    return any(re.search(rf"\b{re.escape(t)}\b", text) for t in terms)


def _sector_from_question(question: str) -> str | None:
    q = question.lower()
    for keyword, sector in _SECTOR_KEYWORDS.items():
        if re.search(rf"\b{re.escape(keyword)}\b", q):
            return sector
    return None


def detect_intent(question: str, symbols: list[str]) -> str:
    """Classify what kind of question this is, so the pipeline gathers the right
    context and the fallback builder picks the right response shape."""
    q = question.lower()
    if _contains_any_term(q, _PORTFOLIO_TERMS):
        return INTENT_PORTFOLIO
    if not symbols and _contains_any_term(q, _RECOMMENDATION_TERMS) and _sector_from_question(question):
        return INTENT_SECTOR
    if len(symbols) >= 2:
        return INTENT_COMPARISON
    if not symbols and _contains_any_term(q, _INDICATOR_TERMS):
        return INTENT_INDICATOR
    if not symbols and _contains_any_term(q, _MARKET_TERMS):
        return INTENT_MARKET
    if symbols:
        return INTENT_SINGLE_STOCK
    return INTENT_GENERAL


# --- Conversation memory ---------------------------------------------------------------

_FOLLOWUP_LEAD_PHRASES = ("what about", "how about", "and what about", "what of")


@dataclass
class ConversationMemory:
    """What the chat remembers between turns: the symbols currently "in view" and the
    intent of the last turn, so a short follow-up ("What about Infosys?", "Which one is
    safer?") resolves without the user repeating context. Owned by the caller (the
    Streamlit page keeps it in st.session_state) -- this module only reads/returns it,
    never touches Streamlit itself.
    """

    symbols: list[str] = field(default_factory=list)
    last_intent: str | None = None


def _looks_like_bare_followup(question: str) -> bool:
    q = question.strip().lower()
    return any(q.startswith(p) for p in _FOLLOWUP_LEAD_PHRASES)


def resolve_symbols_with_memory(question: str, memory: ConversationMemory) -> tuple[list[str], bool]:
    """Combine this turn's extracted symbols with prior-turn memory.

    Returns (effective_symbols, used_memory):
    - No company named this turn -> inherit whatever was previously in view entirely
      (e.g. "Which one is safer?" after a TCS-vs-Infosys comparison).
    - Exactly one new company named, in a short "what about X" style follow-up, with
      something already in memory -> treat as adding to that context (implicit
      comparison), e.g. "Analyze TCS" then "What about Infosys?".
    - Otherwise -> this turn's own symbols stand alone; memory isn't used.
    """
    found = extract_symbols(question)

    if not found:
        return list(memory.symbols), bool(memory.symbols)

    if len(found) == 1 and memory.symbols and _looks_like_bare_followup(question):
        prior = [s for s in memory.symbols if s not in found]
        combined = (prior[:1] + found) if prior else found
        return combined, True

    return found, False


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
    # Question verbs used throughout this app's own examples ("Analyze Wipro", "Predict
    # Wipro tomorrow", "Review portfolio") -- without these, a verb like "analyze" can
    # itself fuzzy-match an unrelated NSE company (e.g. "Analyze" ~ "Ansal Properties" /
    # ANSALAPI.NS at a high Levenshtein score) and get extracted as if it were the
    # company being asked about.
    "analyze", "analyse", "analysis", "predict", "prediction", "review",
    # Indicator/metric jargon must never be mistaken for a company reference -- e.g.
    # "RSI" is a literal substring of "PERSISTENT" (Persistent Systems), so "Explain
    # RSI" would otherwise extract PERSISTENT.NS as if the user had named that company.
    "rsi", "macd", "atr", "adx", "vwap", "bollinger", "sharpe", "drawdown",
    "support", "resistance", "volatility",
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


# --- Context gathering -----------------------------------------------------------------


def _calendar_context() -> dict:
    """Current IST date/time, session status, and next/previous trading day -- included
    in every response so the AI never has to guess "is the market open" or say
    "tomorrow" when tomorrow might be a weekend or exchange holiday."""
    status = get_nse_market_status()
    return {
        "current_date": status.current_time_ist.strftime("%A, %d %B %Y"),
        "current_time_ist": status.current_time_ist.strftime("%H:%M IST"),
        "market_status": status.label,
        "is_trading_day_today": status.is_trading_day,
        "next_trading_session": status.next_trading_day.strftime("%A, %d %B %Y"),
    }


def _symbol_context(symbol: str) -> dict:
    """Grounding data for one symbol: latest price/indicators, fundamentals, sentiment,
    and ML view.

    `symbol` (the canonical `.NS`/`.BO` form) is used for the actual DB/network lookups,
    but never appears in the returned dict -- this dict gets embedded verbatim into
    Gemini's prompt text, and an LLM will readily echo back whatever's in its context,
    so the suffix has to be stripped here rather than trusted to be stripped later.
    """
    history = get_price_history(symbol)
    if history.empty:
        return {"symbol": display_symbol(symbol), "available": False}

    close = history["close"]
    last_date = history.index[-1]
    context: dict = {
        "symbol": display_symbol(symbol),
        "available": True,
        "last_close": round(float(close.iloc[-1]), 2),
        "last_updated": last_date.strftime("%d %b %Y") if hasattr(last_date, "strftime") else str(last_date),
        "1d_change_pct": round(float(close.iloc[-1] / close.iloc[-2] - 1), 4) if len(close) > 1 else None,
        "volume": int(history["volume"].iloc[-1]),
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
    sr = support_resistance(history["high"], history["low"], window=20).dropna()
    if len(sr) > 0:
        context["support"] = round(float(sr["support"].iloc[-1]), 2)
        context["resistance"] = round(float(sr["resistance"].iloc[-1]), 2)

    fundamentals = get_fundamentals(symbol)
    if fundamentals.available:
        context["pe_ratio"] = round(fundamentals.pe_ratio, 1) if fundamentals.pe_ratio is not None else None
        context["market_cap"] = fundamentals.market_cap
        # Computed here (dividend rate / current price) rather than trusting yfinance's
        # own pre-computed `dividendYield` field, which was confirmed empirically to
        # disagree with this calculation for some tickers.
        if fundamentals.dividend_rate is not None and context["last_close"]:
            context["dividend_yield"] = round(fundamentals.dividend_rate / context["last_close"], 4)
        else:
            context["dividend_yield"] = None
        context["52w_high"] = fundamentals.fifty_two_week_high
        context["52w_low"] = fundamentals.fifty_two_week_low

    sentiment_rows = get_stored_sentiment(symbol)
    if sentiment_rows:
        scores = [r["sentiment"] for r in sentiment_rows]
        context["mean_sentiment"] = round(sum(scores) / len(scores), 2)
        context["sentiment_article_count"] = len(scores)
        context["recent_headlines"] = [r["headline"] for r in sentiment_rows[:3]]

    prediction = predict_next_direction(history)
    if prediction is not None:
        predicted_up, probability_up = prediction
        context["ml_next_session_direction"] = "up" if predicted_up else "down"
        context["ml_probability_up"] = round(float(probability_up), 3)
        context["ml_prediction_target_session"] = prediction_target_session().strftime("%A, %d %B %Y")
        context["ml_confidence_caveat"] = (
            "Historical accuracy for daily-direction models is typically only 52-58%, barely "
            "better than a coin flip -- this is a probabilistic lean, not a certainty."
        )

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
    for label, symbol in (("nifty_50", BENCHMARK_NIFTY50), ("sensex", BENCHMARK_SENSEX), ("bank_nifty", BENCHMARK_BANKNIFTY)):
        history = get_price_history(symbol)
        if not history.empty and len(history) > 1:
            close = history["close"]
            context[label] = {
                "last_close": round(float(close.iloc[-1]), 2),
                "1d_change_pct": round(float(close.iloc[-1] / close.iloc[-2] - 1), 4),
            }
    return context


def _sector_candidates_context(sector: str, limit: int = 5) -> list[dict]:
    """Ranked snapshot of NSE stocks in `sector` that FinSight already has price history
    for -- never expands the universe by fetching new tickers just to answer a "best
    stock" question, so the comparison stays grounded in real, already-ingested data."""
    candidates: list[dict] = []
    for symbol in list_ticker_symbols():
        info = get_ticker_info(symbol)
        if not info or info.get("sector") != sector:
            continue
        history = get_price_history(symbol)
        if history.empty or len(history) < 2:
            continue
        close = history["close"]
        entry: dict = {
            "symbol": display_symbol(symbol),
            "last_close": round(float(close.iloc[-1]), 2),
            "1m_change_pct": round(float(close.iloc[-1] / close.iloc[-22] - 1), 4) if len(close) > 22 else None,
        }
        rsi_series = rsi(close, window=14).dropna()
        if len(rsi_series) > 0:
            entry["rsi_14"] = round(float(rsi_series.iloc[-1]), 1)
        sentiment_rows = get_stored_sentiment(symbol)
        if sentiment_rows:
            entry["mean_sentiment"] = round(sum(r["sentiment"] for r in sentiment_rows) / len(sentiment_rows), 2)
        prediction = predict_next_direction(history)
        if prediction is not None:
            entry["ml_probability_up"] = round(float(prediction[1]), 3)
        candidates.append(entry)
    candidates.sort(key=lambda c: c.get("1m_change_pct") if c.get("1m_change_pct") is not None else -999, reverse=True)
    return candidates[:limit]


def gather_context(question: str, memory: ConversationMemory | None = None) -> dict:
    """Assemble grounding data for a question: calendar/session status (always),
    per-symbol data for any tickers mentioned or carried over from conversation memory,
    market context, portfolio context, and (for "best sector stock" questions) a
    same-sector comparison.

    Keyed by display symbol (no `.NS`/`.BO`) throughout, not the canonical form used
    internally for lookups -- this dict is what gets embedded in the Gemini prompt.
    """
    memory = memory or ConversationMemory()
    symbols, used_memory = resolve_symbols_with_memory(question, memory)
    intent = detect_intent(question, symbols)

    context: dict = {
        "mentioned_symbols": [display_symbol(s) for s in symbols],
        "intent": intent,
        "used_conversation_memory": used_memory,
        "calendar": _calendar_context(),
    }
    for symbol in symbols:
        context[display_symbol(symbol)] = _symbol_context(symbol)
    context["market"] = _market_context()

    portfolio_context = _portfolio_context()
    if portfolio_context:
        context.update(portfolio_context)

    if intent == INTENT_SECTOR:
        sector = _sector_from_question(question)
        if sector:
            context["sector_query"] = sector
            context["sector_candidates"] = _sector_candidates_context(sector)

    return context


def _update_memory(question: str, memory: ConversationMemory, intent: str) -> ConversationMemory:
    symbols, _used_memory = resolve_symbols_with_memory(question, memory)
    return ConversationMemory(symbols=(symbols or memory.symbols)[:5], last_intent=intent)


# --- Structured, rule-based fallback (never blank, never generic) ---------------------

_INDICATOR_GLOSSARY: dict[str, tuple[str, str]] = {
    "rsi": (
        "RSI is like a speedometer for buying and selling, scored 0 to 100. Above 70 "
        "usually means people have been buying really fast (it might be getting \"too "
        "hot\"); below 30 means people have been selling fast (it might be oversold). "
        "Around 50 is calm, normal trading.",
        "RSI (Relative Strength Index, 14-period) measures the speed and magnitude of "
        "recent price changes on a 0-100 scale. Readings above 70 suggest overbought "
        "conditions, below 30 oversold -- a mean-reversion signal that can stay extended "
        "during strong trends.",
    ),
    "macd": (
        "MACD compares two moving averages (a faster one and a slower one) to spot when "
        "momentum is shifting. When the faster line crosses above the slower one, that's "
        "often read as a sign momentum is turning up; crossing below, turning down.",
        "MACD (Moving Average Convergence Divergence) is the difference between a "
        "12-period and 26-period EMA, with a 9-period EMA signal line. A bullish "
        "crossover (MACD > signal) suggests upward momentum; a bearish crossover the "
        "opposite.",
    ),
    "bollinger": (
        "Bollinger Bands draw a normal price range around the stock, like guardrails. "
        "When the price touches the top guardrail, it's trading unusually high recently; "
        "the bottom guardrail means unusually low.",
        "Bollinger Bands plot a simple moving average with upper/lower bands at N "
        "standard deviations (default 20-period, 2 sigma). Price touching a band flags a "
        "statistical extreme relative to recent volatility, not necessarily a reversal.",
    ),
    "vwap": (
        "VWAP is the average price a stock traded at today, weighted by how many shares "
        "changed hands at each price. Traders use it to judge if the current price is "
        "cheap or expensive versus the day's typical price.",
        "VWAP (Volume-Weighted Average Price) weights each trade's price by its volume "
        "over the window. Price above VWAP suggests buyers are in control intraday; "
        "below suggests sellers.",
    ),
    "atr": (
        "ATR measures how much a stock typically moves in a day, in rupees -- a bigger "
        "ATR means bigger daily swings, up or down.",
        "ATR (Average True Range, 14-period) measures average daily volatility in "
        "absolute price terms, accounting for gaps. Higher ATR implies wider "
        "stop-loss/position-sizing distances are needed.",
    ),
    "adx": (
        "ADX tells you how strong a trend is, not which direction. A high ADX means the "
        "stock is trending strongly (up or down); a low ADX means it's moving sideways.",
        "ADX (Average Directional Index, 14-period) measures trend strength on a 0-100 "
        "scale regardless of direction. Above 25 is often read as a meaningfully "
        "trending market; below 20 as range-bound/choppy.",
    ),
    "sharpe": (
        "Sharpe ratio tells you how much reward you got for the bumpiness of the ride. A "
        "higher number means better returns for the risk taken.",
        "Sharpe ratio is (mean excess return) / (return standard deviation), annualized. "
        "Above 1.0 is generally considered good risk-adjusted performance.",
    ),
    "drawdown": (
        "Max drawdown is the biggest drop from a peak to a bottom this stock or "
        "portfolio has seen -- a way to measure the worst pain an investor would have "
        "felt.",
        "Max drawdown is the largest peak-to-trough percentage decline over the "
        "observed period -- a standard downside-risk measure independent of when it "
        "occurred.",
    ),
    "support": (
        "Support is a price level where a stock has tended to stop falling and bounce "
        "back up in the past -- like a floor.",
        "Support is a price level where historical buying pressure has repeatedly "
        "halted declines -- often the recent rolling low.",
    ),
    "resistance": (
        "Resistance is a price level where a stock has tended to stop rising and pull "
        "back in the past -- like a ceiling.",
        "Resistance is a price level where historical selling pressure has repeatedly "
        "capped rallies -- often the recent rolling high.",
    ),
    "volatility": (
        "Volatility measures how bumpy the ride is -- how much the price swings up and "
        "down. Higher volatility means bigger, faster moves in either direction.",
        "Volatility (annualized 20-day rolling standard deviation of returns) measures "
        "the dispersion of returns -- higher values imply larger expected price swings, "
        "not direction.",
    ),
}

_DISCLAIMER = "This is educational information, not financial advice -- always do your own research or consult a licensed advisor."


def _calendar_header(calendar: dict) -> str:
    if not calendar:
        return ""
    return f"*{calendar.get('current_date', '')}, {calendar.get('current_time_ist', '')} — {calendar.get('market_status', '')}.*"


def _fallback_single_stock(symbol_display: str, data: dict, mode: str) -> str:
    if not data or not data.get("available"):
        return (
            f"I don't have price data for {symbol_display} yet. Try adding it on the "
            "Stock Analysis or Portfolio page first."
        )

    is_simple = mode == "Simple"
    lines: list[str] = []

    change = data.get("1d_change_pct")
    if change is not None:
        change_word = "up" if change >= 0 else "down"
        lines.append(
            f"**{symbol_display}** is at ₹{data['last_close']:,.2f}, {change_word} {abs(change):.1%} "
            f"today (last updated {data.get('last_updated', 'recently')})."
        )
    else:
        lines.append(f"**{symbol_display}** is at ₹{data['last_close']:,.2f} (last updated {data.get('last_updated', 'recently')}).")

    tech_bits = []
    if data.get("rsi_14") is not None:
        e = explain_rsi(data["rsi_14"])
        tech_bits.append(e.simple if is_simple else e.professional)
    if data.get("macd") is not None:
        e = explain_macd(data["macd"], data.get("macd_signal"))
        tech_bits.append(e.simple if is_simple else e.professional)
    if data.get("support") is not None:
        e = explain_support(data["last_close"], data["support"])
        tech_bits.append(e.simple if is_simple else e.professional)
    if data.get("resistance") is not None:
        e = explain_resistance(data["last_close"], data["resistance"])
        tech_bits.append(e.simple if is_simple else e.professional)
    if tech_bits:
        lines.append(" ".join(tech_bits))

    if data.get("pe_ratio") is not None or data.get("dividend_yield") is not None:
        e = explain_fundamentals(data.get("pe_ratio"), data.get("dividend_yield"))
        lines.append(e.simple if is_simple else e.professional)

    if data.get("mean_sentiment") is not None:
        e = explain_sentiment(data["mean_sentiment"])
        headline_bit = f" Recent headline: \"{data['recent_headlines'][0]}\"." if data.get("recent_headlines") else ""
        lines.append((e.simple if is_simple else e.professional) + headline_bit)

    if data.get("ml_next_session_direction") is not None:
        target = data.get("ml_prediction_target_session", "the next trading session")
        e = explain_ml_prediction(data["ml_next_session_direction"] == "up", data["ml_probability_up"], 0.55, target)
        lines.append(e.simple if is_simple else e.professional)
        if not is_simple and data.get("ml_confidence_caveat"):
            lines.append(data["ml_confidence_caveat"])

    lines.append(_DISCLAIMER)
    return "\n\n".join(l for l in lines if l)


def _fallback_comparison(context: dict, mode: str) -> str:
    symbols = context.get("mentioned_symbols", [])
    lines = [_fallback_single_stock(s, context.get(s, {}), mode) for s in symbols]

    rows = [(s, context.get(s, {})) for s in symbols if context.get(s, {}).get("available")]
    if len(rows) >= 2:
        comp_bits = []
        for s, d in rows:
            bits = []
            if d.get("rsi_14") is not None:
                bits.append(f"RSI {d['rsi_14']}")
            if d.get("mean_sentiment") is not None:
                bits.append(f"sentiment {d['mean_sentiment']:+.2f}")
            if d.get("ml_next_session_direction"):
                bits.append(f"model leans {d['ml_next_session_direction']} ({d['ml_probability_up']:.0%})")
            comp_bits.append(f"{s} — " + ", ".join(bits) if bits else s)
        lines.append("**Side by side:** " + " | ".join(comp_bits))
    return "\n\n".join(lines)


def _fallback_portfolio(context: dict, mode: str) -> str:
    portfolios = context.get("portfolios", [])
    if not portfolios:
        return "You don't have any portfolios set up yet. Add one on the Portfolio page to get a review here."
    lines = []
    for p in portfolios:
        holdings = p.get("holdings", [])
        if not holdings:
            lines.append(f"**{p['name']}** has no holdings yet.")
            continue
        names = ", ".join(h["symbol"] for h in holdings)
        lines.append(f"**{p['name']}** holds {len(holdings)} position(s): {names}.")
    lines.append(
        "For Sharpe ratio, max drawdown, sector allocation, diversification score, and "
        "a risk meter, see the Portfolio page directly -- those are computed there, not "
        "recomputed here."
    )
    lines.append(_DISCLAIMER)
    return "\n\n".join(lines)


def _fallback_market(context: dict, mode: str) -> str:
    market = context.get("market", {})
    snapshot = MarketSnapshot(
        nifty_pct=(market.get("nifty_50") or {}).get("1d_change_pct"),
        sensex_pct=(market.get("sensex") or {}).get("1d_change_pct"),
        banknifty_pct=(market.get("bank_nifty") or {}).get("1d_change_pct"),
    )
    return rule_based_summary(snapshot) + "\n\n" + _DISCLAIMER


def _detect_indicator_term(question: str) -> str | None:
    q = question.lower()
    for term in _INDICATOR_GLOSSARY:
        if re.search(rf"\b{re.escape(term)}\b", q):
            return term
    return None


def _fallback_indicator(question: str, context: dict, mode: str) -> str:
    term = _detect_indicator_term(question)
    if term is None:
        return _fallback_general(context, mode)
    simple, professional = _INDICATOR_GLOSSARY[term]
    text = simple if mode == "Simple" else professional
    symbols = context.get("mentioned_symbols", [])
    if symbols:
        data = context.get(symbols[0], {})
        value = data.get(f"{term}_14") if f"{term}_14" in data else data.get(term)
        if value is not None:
            text += f" Right now for {symbols[0]}, that reading is {value}."
    return text + "\n\n" + _DISCLAIMER


def _fallback_sector(context: dict, mode: str) -> str:
    sector = context.get("sector_query")
    candidates = context.get("sector_candidates", [])
    if not candidates:
        return (
            f"I don't have enough tracked {sector or 'sector'} stocks with price history "
            "yet to compare. Try adding a few on the Market Overview or Portfolio page first."
        )
    lines = [f"Here's what FinSight already tracks in {sector} -- this is informational, not a ranking of which is \"best\":"]
    for c in candidates:
        bits = [f"₹{c['last_close']:,.2f}"]
        if c.get("1m_change_pct") is not None:
            bits.append(f"{c['1m_change_pct']:+.1%} over 1M")
        if c.get("rsi_14") is not None:
            bits.append(f"RSI {c['rsi_14']}")
        if c.get("mean_sentiment") is not None:
            bits.append(f"sentiment {c['mean_sentiment']:+.2f}")
        if c.get("ml_probability_up") is not None:
            bits.append(f"model {c['ml_probability_up']:.0%} up-lean")
        lines.append(f"- **{c['symbol']}**: " + ", ".join(bits))
    lines.append("\"Best\" depends on your goals and risk tolerance -- this is a data summary, not financial advice.")
    return "\n".join(lines)


def _fallback_general(context: dict, mode: str) -> str:
    symbols = context.get("mentioned_symbols", [])
    if not symbols:
        return (
            "I couldn't find a specific stock in your question, and AI narration isn't "
            "available right now. Try naming a company (e.g. \"Reliance\" or \"TCS\"), or "
            "browse the Market Overview / Stock Analysis pages directly."
        )
    if len(symbols) >= 2:
        return _fallback_comparison(context, mode)
    return _fallback_single_stock(symbols[0], context.get(symbols[0], {}), mode)


def _build_fallback_answer(question: str, context: dict, mode: str) -> str:
    """A structured, section-based answer used when Gemini is unavailable or fails --
    never blank, never a generic one-liner. Built entirely from real, already-computed
    numbers via core.explain, dispatched by the detected intent."""
    intent = context.get("intent", INTENT_GENERAL)
    if intent == INTENT_PORTFOLIO:
        body = _fallback_portfolio(context, mode)
    elif intent == INTENT_MARKET:
        body = _fallback_market(context, mode)
    elif intent == INTENT_INDICATOR:
        body = _fallback_indicator(question, context, mode)
    elif intent == INTENT_SECTOR:
        body = _fallback_sector(context, mode)
    elif intent == INTENT_COMPARISON:
        body = _fallback_comparison(context, mode)
    elif intent == INTENT_SINGLE_STOCK:
        symbols = context.get("mentioned_symbols", [])
        body = _fallback_single_stock(symbols[0], context.get(symbols[0], {}), mode) if symbols else _fallback_general(context, mode)
    else:
        body = _fallback_general(context, mode)

    calendar_line = _calendar_header(context.get("calendar", {}))
    return f"{calendar_line}\n\n{body}" if calendar_line else body


# Kept for backward compatibility with callers/tests that only need a data-dump, not the
# full structured, intent-aware answer.
def _fallback_answer(question: str, context: dict) -> str:
    return _build_fallback_answer(question, context, "Professional")


# --- Gemini path ------------------------------------------------------------------------

_RESPONSE_FORMAT_INSTRUCTIONS = (
    "Structure your answer in this order (skip a section if the data for it isn't "
    "present, but keep the order): executive summary; current market context (date, "
    "time, session status); live market data (price, volume, last-updated); technical "
    "analysis; fundamental analysis; news & sentiment; ML prediction (state its "
    "confidence, why that confidence is limited, and an alternative scenario); risks; "
    "a beginner-friendly plain-language recap covering what happened, why, and one "
    "thing to learn next; and an educational disclaimer. Not every section needs equal depth."
)


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
        "You are FinSight's AI analyst for Indian equities (NSE/BSE) -- never a generic "
        "chatbot. Answer the user's question using ONLY the real data below -- never "
        "invent a number, price, news item, probability, or date not present here. If "
        "something needed to answer isn't present, say exactly what's missing instead of "
        "guessing. Never say \"tomorrow\" -- the data below already gives you the correct "
        "next trading session date; use that exact date instead. If the market is "
        "currently closed, say so before giving any analysis.\n\n"
        f"User question: {question}\n\n"
        f"Available data: {context}\n\n"
        f"{_RESPONSE_FORMAT_INSTRUCTIONS}\n\n"
        f"{style} Keep it under 220 words. Never give direct buy/sell orders -- frame "
        "everything as informational, and end with a brief reminder this isn't financial advice."
    )
    response = model.generate_content(prompt, request_options={"timeout": GEMINI_TIMEOUT_SECONDS})
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned an empty chat answer")
    return text


def answer_question(
    question: str, mode: str, memory: ConversationMemory | None = None
) -> tuple[str, bool, ConversationMemory]:
    """Returns (answer, used_gemini, updated_memory). Always returns a non-empty,
    data-grounded, structured answer -- routed through intent detection, the market
    calendar, live technical/fundamental/sentiment/ML data, portfolio context, and
    conversation memory before Gemini (or the fallback) ever narrates it."""
    memory = memory or ConversationMemory()
    context = gather_context(question, memory)
    intent = context["intent"]
    updated_memory = _update_memory(question, memory, intent)

    fallback = _build_fallback_answer(question, context, mode)
    if GEMINI_API_KEY:
        try:
            return _gemini_answer(question, context, mode), True, updated_memory
        except Exception as exc:  # Gemini errors must never break the chat
            logger.warning("Gemini chat answer failed, falling back to structured data dump: %s", exc)
    return fallback, False, updated_memory
