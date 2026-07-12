"""Plain-language explanations for every metric, indicator, and AI/ML output in the app.

Every explainer returns an `Explanation` with a Simple Mode sentence a 10-year-old with
no finance background could follow, a Professional Mode sentence with the technical
detail, and a `mood` ("good"/"neutral"/"worried") the UI can use to color/badge it.
Pure functions, no Streamlit or DB coupling, so they're unit-testable and reusable from
any page or the "Ask FinSight AI" chat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mood = Literal["good", "neutral", "worried"]


@dataclass(frozen=True)
class Explanation:
    """A metric's meaning in both modes, plus a mood for UI coloring."""

    simple: str
    professional: str
    mood: Mood


def explain_rsi(value: float | None) -> Explanation:
    """RSI (Relative Strength Index): how fast people have been buying vs selling lately."""
    if value is None:
        return Explanation(
            "We don't have enough days of data yet to tell you this.",
            "Insufficient history to compute RSI(14).",
            "neutral",
        )
    if value >= 70:
        return Explanation(
            "Lots of people have been buying this stock really fast lately. When that happens "
            "too fast, sometimes people stop buying for a bit — kind of like getting tired "
            "after running. That can mean the price cools off soon.",
            f"RSI is {value:.0f}, in overbought territory (>=70). Momentum has been strongly "
            "positive; a pullback or consolidation is not guaranteed but is common from these levels.",
            "worried",
        )
    if value <= 30:
        return Explanation(
            "Lots of people have been selling this stock really fast lately — kind of like a "
            "toy nobody wants to play with right now. Sometimes that means it's gotten too cheap "
            "and buyers start coming back.",
            f"RSI is {value:.0f}, in oversold territory (<=30). Selling pressure has been strong; "
            "a bounce is common from these levels but not guaranteed.",
            "neutral",
        )
    return Explanation(
        f"Buying and selling have been pretty balanced lately (score: {value:.0f} out of 100). "
        "Nothing unusual going on here.",
        f"RSI is {value:.0f}, in the neutral 30-70 band — no strong overbought/oversold signal.",
        "neutral",
    )


def explain_macd(macd_value: float | None, signal_value: float | None) -> Explanation:
    """MACD: whether the stock's momentum is speeding up or slowing down."""
    if macd_value is None or signal_value is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute MACD.",
            "neutral",
        )
    above = macd_value > signal_value
    if above and macd_value > 0:
        return Explanation(
            "This stock has been picking up speed, like a bike going downhill. Recent momentum is "
            "pushing the price up.",
            f"MACD ({macd_value:.2f}) is above its signal line ({signal_value:.2f}) and positive -- "
            "a bullish momentum signal.",
            "good",
        )
    if above:
        return Explanation(
            "This stock's slide downhill is starting to slow down, like a bike losing speed — "
            "it might be turning around soon.",
            f"MACD ({macd_value:.2f}) is above its signal line ({signal_value:.2f}) but still "
            "negative -- downward momentum may be fading.",
            "neutral",
        )
    if macd_value < 0:
        return Explanation(
            "This stock has been losing speed, like a bike rolling downhill and slowing to a stop. "
            "Momentum is pointing down.",
            f"MACD ({macd_value:.2f}) is below its signal line ({signal_value:.2f}) and negative -- "
            "a bearish momentum signal.",
            "worried",
        )
    return Explanation(
        "This stock's climb is starting to lose steam, like a bike running out of push uphill.",
        f"MACD ({macd_value:.2f}) is below its signal line ({signal_value:.2f}) but still positive -- "
        "upward momentum may be fading.",
        "neutral",
    )


def explain_support(current_price: float | None, support: float | None) -> Explanation:
    """Support level: the price where buyers usually step in."""
    if support is None or current_price is None:
        return Explanation(
            "We don't have enough data yet to find this.",
            "Insufficient history to compute a rolling support level.",
            "neutral",
        )
    return Explanation(
        "This is a price where people usually decide 'this is cheap, I'll buy' — so it doesn't "
        "often go much lower than this.",
        f"Rolling support (trailing-window low) is at {support:.2f}, "
        f"{(current_price / support - 1) * 100:+.1f}% below the current price.",
        "neutral",
    )


def explain_resistance(current_price: float | None, resistance: float | None) -> Explanation:
    """Resistance level: the price where sellers usually step in."""
    if resistance is None or current_price is None:
        return Explanation(
            "We don't have enough data yet to find this.",
            "Insufficient history to compute a rolling resistance level.",
            "neutral",
        )
    return Explanation(
        "This is a price where people usually decide 'this is expensive, I'll sell' — so it "
        "doesn't often go much higher than this.",
        f"Rolling resistance (trailing-window high) is at {resistance:.2f}, "
        f"{(resistance / current_price - 1) * 100:+.1f}% above the current price.",
        "neutral",
    )


def explain_bollinger(current_price: float | None, upper: float | None, lower: float | None) -> Explanation:
    """Bollinger Bands: whether the price is stretched unusually far from its normal range."""
    if current_price is None or upper is None or lower is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute Bollinger Bands.",
            "neutral",
        )
    if current_price >= upper:
        return Explanation(
            "The price has stretched further from its usual range than normal, like a rubber "
            "band pulled tight — it's near the top of where it usually trades.",
            f"Price ({current_price:.2f}) is at/above the upper Bollinger Band ({upper:.2f}) -- "
            "trading near the top of its recent volatility range.",
            "worried",
        )
    if current_price <= lower:
        return Explanation(
            "The price has stretched further from its usual range than normal, like a rubber "
            "band pulled tight — it's near the bottom of where it usually trades.",
            f"Price ({current_price:.2f}) is at/below the lower Bollinger Band ({lower:.2f}) -- "
            "trading near the bottom of its recent volatility range.",
            "neutral",
        )
    return Explanation(
        "The price is trading comfortably within its usual day-to-day range — nothing stretched "
        "or unusual right now.",
        f"Price ({current_price:.2f}) is within its Bollinger Bands ({lower:.2f}-{upper:.2f}).",
        "good",
    )


def explain_volatility(value: float | None) -> Explanation:
    """Annualized volatility: how bumpy the ride has been."""
    if value is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute rolling volatility.",
            "neutral",
        )
    if value >= 0.45:
        return Explanation(
            f"This stock's price has been jumping around a lot lately ({value:.0%} a year) — "
            "like a bumpy rollercoaster ride. It can move a lot in either direction, fast.",
            f"Annualized 20-day volatility is {value:.1%}, on the high end for a large-cap equity.",
            "worried",
        )
    if value <= 0.20:
        return Explanation(
            f"This stock's price has been pretty calm lately ({value:.0%} a year) — like a gentle "
            "walk, not a rollercoaster.",
            f"Annualized 20-day volatility is {value:.1%}, relatively low.",
            "good",
        )
    return Explanation(
        f"This stock moves around a normal, moderate amount ({value:.0%} a year) — not too wild, "
        "not too calm.",
        f"Annualized 20-day volatility is {value:.1%}, a moderate level.",
        "neutral",
    )


def explain_atr(value: float | None, current_price: float | None) -> Explanation:
    """ATR (Average True Range): the typical size of a day's price swing, in rupees."""
    if value is None or current_price is None or current_price == 0:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute ATR(14).",
            "neutral",
        )
    pct = value / current_price
    mood: Mood = "worried" if pct >= 0.03 else "good" if pct <= 0.015 else "neutral"
    return Explanation(
        f"On a typical day, this stock's price moves up or down by about ₹{value:.2f} "
        f"({pct:.1%} of its price) — that's how big its normal wiggles are.",
        f"ATR(14) is {value:.2f} ({pct:.1%} of current price) -- the average daily trading range.",
        mood,
    )


def explain_adx(value: float | None) -> Explanation:
    """ADX (Average Directional Index): how strongly the stock is trending, either way."""
    if value is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute ADX(14).",
            "neutral",
        )
    if value >= 25:
        return Explanation(
            "This stock is moving pretty firmly in one direction right now, like a wagon rolling "
            "steadily down a hill instead of wobbling side to side.",
            f"ADX is {value:.0f} (>=25), indicating a trending market (direction not implied by ADX alone).",
            "neutral",
        )
    return Explanation(
        "This stock isn't clearly heading anywhere right now — it's more wobbling side to side "
        "than rolling in one direction.",
        f"ADX is {value:.0f} (<25), indicating a weak or absent trend / range-bound conditions.",
        "neutral",
    )


def explain_vwap(current_price: float | None, vwap_value: float | None) -> Explanation:
    """Rolling VWAP: the "fair" average price recently, weighted by how much traded at each price."""
    if current_price is None or vwap_value is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute rolling VWAP.",
            "neutral",
        )
    if current_price > vwap_value:
        return Explanation(
            "Right now the price is a bit above what most people have actually been paying "
            "recently — like paying more than the average price at a shop.",
            f"Price ({current_price:.2f}) is above the rolling VWAP ({vwap_value:.2f}).",
            "neutral",
        )
    return Explanation(
        "Right now the price is a bit below what most people have actually been paying recently "
        "— like getting a small discount compared to the average shopper.",
        f"Price ({current_price:.2f}) is at/below the rolling VWAP ({vwap_value:.2f}).",
        "good",
    )


def explain_sharpe(value: float | None) -> Explanation:
    """Sharpe ratio: return earned per unit of bumpiness/risk taken."""
    if value is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute a Sharpe ratio.",
            "neutral",
        )
    if value >= 1.0:
        return Explanation(
            f"For the bumpiness of the ride, this portfolio has been earning a good reward "
            f"(score: {value:.2f}) — good return for the risk taken.",
            f"Sharpe ratio is {value:.2f} (>=1.0), a good risk-adjusted return historically.",
            "good",
        )
    if value <= 0:
        return Explanation(
            f"This portfolio hasn't been rewarding the bumpiness of the ride (score: {value:.2f}) "
            "— the risk taken hasn't paid off so far.",
            f"Sharpe ratio is {value:.2f} (<=0), meaning risk-adjusted returns have been poor "
            "or negative historically.",
            "worried",
        )
    return Explanation(
        f"This portfolio has earned a so-so reward for its bumpiness (score: {value:.2f}) — not "
        "bad, not great.",
        f"Sharpe ratio is {value:.2f}, a moderate risk-adjusted return historically.",
        "neutral",
    )


def explain_drawdown(value: float | None) -> Explanation:
    """Max drawdown: the worst drop from a peak this portfolio has seen."""
    if value is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient history to compute max drawdown.",
            "neutral",
        )
    pct = abs(value)
    if pct >= 0.30:
        fraction_word = "about half" if pct >= 0.45 else "more than a third" if pct >= 0.35 else "almost a third"
        return Explanation(
            f"At its worst point, this portfolio dropped {pct:.0%} from its peak — like losing "
            f"{fraction_word} of your savings jar before it started refilling. That's a big dip.",
            f"Max drawdown is {value:.1%}, a severe historical peak-to-trough decline.",
            "worried",
        )
    if pct <= 0.10:
        return Explanation(
            f"At its worst point, this portfolio only dropped {pct:.0%} from its peak — a pretty "
            "small dip, like a gentle bump in the road.",
            f"Max drawdown is {value:.1%}, a mild historical peak-to-trough decline.",
            "good",
        )
    return Explanation(
        f"At its worst point, this portfolio dropped {pct:.0%} from its peak — a noticeable but "
        "not extreme dip.",
        f"Max drawdown is {value:.1%}, a moderate historical peak-to-trough decline.",
        "neutral",
    )


def explain_diversification(score: float | None) -> Explanation:
    """Diversification score (0-100): how spread out the portfolio is, not just how many stocks it holds."""
    if score is None:
        return Explanation(
            "We don't have enough data yet to tell you this.",
            "Insufficient holdings to compute a diversification score.",
            "neutral",
        )
    if score >= 70:
        return Explanation(
            f"Your money is spread out nicely across your holdings (score: {score:.0f}/100) — if one "
            "stock has a bad day, it probably won't sink the whole portfolio.",
            f"Diversification score {score:.0f}/100 (from position-weight HHI) -- value is well "
            "distributed across holdings, limiting single-name concentration risk.",
            "good",
        )
    if score < 40:
        return Explanation(
            f"A lot of your money is riding on just one or two stocks (score: {score:.0f}/100) — if one "
            "of them drops a lot, your whole portfolio feels it.",
            f"Diversification score {score:.0f}/100 (from position-weight HHI) -- value is concentrated "
            "in a small number of holdings, raising single-name risk.",
            "worried",
        )
    return Explanation(
        f"Your money is somewhat spread out (score: {score:.0f}/100), but a few stocks still carry more "
        "weight than the rest.",
        f"Diversification score {score:.0f}/100 (from position-weight HHI) -- moderate concentration.",
        "neutral",
    )


def explain_risk_level(level: str, volatility_annualized: float | None) -> Explanation:
    """Portfolio risk band (Low/Medium/High) from annualized volatility."""
    vol_bit = f" (annualized volatility {volatility_annualized:.1%})" if volatility_annualized is not None else ""
    if level == "Low":
        return Explanation(
            "This portfolio's value tends to move fairly gently day to day — a calmer ride "
            "than the typical stock portfolio.",
            f"Risk band: Low{vol_bit}. Below the ~15-35% annualized-volatility range typical of NSE "
            "large/mid-cap equity portfolios.",
            "good",
        )
    if level == "High":
        return Explanation(
            "This portfolio's value can swing a lot day to day — expect a bumpier ride than "
            "average, in both directions.",
            f"Risk band: High{vol_bit}. Above the ~15-35% annualized-volatility range typical of NSE "
            "large/mid-cap equity portfolios.",
            "worried",
        )
    return Explanation(
        "This portfolio's value moves around a normal amount for stocks — not unusually "
        "calm or unusually wild.",
        f"Risk band: Medium{vol_bit}. Within the ~15-35% annualized-volatility range typical of NSE "
        "large/mid-cap equity portfolios.",
        "neutral",
    )


def explain_fundamentals(pe_ratio: float | None, dividend_yield: float | None) -> Explanation:
    """P/E ratio and dividend yield: how expensive the stock is relative to its
    earnings, and how much of a cash payout it offers."""
    if pe_ratio is None and dividend_yield is None:
        return Explanation(
            "We don't have company financial details for this one right now.",
            "Fundamental data (P/E, dividend yield) unavailable for this symbol.",
            "neutral",
        )
    bits_simple: list[str] = []
    bits_pro: list[str] = []
    mood: Mood = "neutral"
    if pe_ratio is not None:
        if pe_ratio <= 0:
            bits_simple.append("the company hasn't been profitable lately, so its price-to-earnings comparison doesn't apply")
            bits_pro.append(f"P/E {pe_ratio:.1f} (negative/not meaningful -- recent earnings are negative)")
            mood = "worried"
        elif pe_ratio > 40:
            bits_simple.append(f"the stock is priced high compared to its profits (P/E {pe_ratio:.1f}) -- investors are paying a lot for future growth")
            bits_pro.append(f"P/E {pe_ratio:.1f}, rich relative to broad NSE large-cap norms (~15-30)")
        elif pe_ratio < 10:
            bits_simple.append(f"the stock is priced cheaply compared to its profits (P/E {pe_ratio:.1f})")
            bits_pro.append(f"P/E {pe_ratio:.1f}, low relative to broad NSE large-cap norms (~15-30)")
        else:
            bits_simple.append(f"the stock's price looks reasonable next to its profits (P/E {pe_ratio:.1f})")
            bits_pro.append(f"P/E {pe_ratio:.1f}, within typical NSE large-cap norms (~15-30)")
    if dividend_yield is not None and dividend_yield > 0:
        bits_simple.append(f"it also pays shareholders about {dividend_yield:.1%} of the share price back each year as a dividend")
        bits_pro.append(f"dividend yield {dividend_yield:.1%}")
    simple = "For this company, " + "; ".join(bits_simple) + "." if bits_simple else "Limited fundamental data available for this company."
    professional = "; ".join(bits_pro) + "." if bits_pro else "Limited fundamental data available."
    return Explanation(simple, professional, mood)


def explain_sentiment(score: float | None) -> Explanation:
    """News sentiment score (-1 to +1): whether recent news reads positive or negative."""
    if score is None:
        return Explanation(
            "We don't have any recent news scored for this yet.",
            "No sentiment data available.",
            "neutral",
        )
    if score > 0.15:
        return Explanation(
            "The recent news stories about this company sound pretty positive overall — like "
            "everyone at school saying good things about the same toy.",
            f"Mean recent sentiment score is {score:+.2f} (range -1 to +1), net positive news tone.",
            "good",
        )
    if score < -0.15:
        return Explanation(
            "The recent news stories about this company sound pretty negative overall — like "
            "everyone complaining about the same toy.",
            f"Mean recent sentiment score is {score:+.2f} (range -1 to +1), net negative news tone.",
            "worried",
        )
    return Explanation(
        "The recent news stories about this company are a mixed bag — some good, some bad, "
        "nothing standing out strongly either way.",
        f"Mean recent sentiment score is {score:+.2f} (range -1 to +1), roughly neutral news tone.",
        "neutral",
    )


def explain_ml_prediction(
    predicted_up: bool,
    probability: float,
    historical_accuracy: float,
    target_session_label: str = "the next trading session",
) -> Explanation:
    """ML direction prediction: what the model is guessing, and how much to trust it.

    `target_session_label` should name the actual next trading session (e.g. "Tuesday, 27
    Jan") -- never "tomorrow", since tomorrow may be a weekend or exchange holiday.
    """
    direction_word = "up" if predicted_up else "down"
    accuracy_out_of_10 = round(historical_accuracy * 10)
    simple = (
        f"Our computer looked at how this stock behaved before and thinks it's a little more "
        f"likely to go {direction_word} in {target_session_label} than the other way — but it's only "
        f"been right about {accuracy_out_of_10} times out of 10 in the past, so this is a guess, not a promise."
    )
    professional = (
        f"Model predicts direction={direction_word} for {target_session_label} with probability "
        f"{probability:.1%}. Historical walk-forward accuracy is {historical_accuracy:.1%} -- barely "
        "above chance for daily equity direction, consistent with published research. Not a trading "
        "signal on its own."
    )
    mood: Mood = "neutral"
    return Explanation(simple, professional, mood)


PREDICTION_DISCLAIMER = (
    "This is educational, not financial advice. Predictions are guesses based on past patterns "
    "and are often wrong -- never the sole basis for a trading decision."
)
