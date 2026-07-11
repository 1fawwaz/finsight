"""AI-narrated explanation panel for analytical pages (Stock Analysis, Portfolio, AI
Sentiment, ML Signals): Gemini synthesizes the page's own already-computed metrics into
a short narrative, adapting to Simple/Professional mode -- grounded only in real numbers
passed in, never invented. Falls back to a rule-based summary (stitched from
core.explain's per-metric text by the caller) if Gemini is unavailable or fails; the
fallback is never blank.
"""

from __future__ import annotations

from core.config import GEMINI_API_KEY, get_logger

logger = get_logger(__name__)

MODEL_NAME = "gemini-flash-latest"

_SIMPLE_STYLE = (
    "Explain like the reader is 10 years old with no finance background at all. Use no "
    "jargon whatsoever -- never say RSI, MACD, volatility, Sharpe, drawdown, overbought, "
    "oversold, or any other technical term. Use everyday analogies (a toy everyone wants, "
    "a bike going downhill, a rollercoaster). Write 3-4 short sentences."
)
_PROFESSIONAL_STYLE = (
    "Write for an experienced trader: what happened, why, your confidence level, key "
    "risks, and which data points you used. Technical language is fine. 3-4 sentences."
)


def _build_prompt(context_label: str, data: dict, mode: str) -> str:
    style = _SIMPLE_STYLE if mode == "Simple" else _PROFESSIONAL_STYLE
    return (
        "You are FinSight's AI analyst for Indian equities (NSE/BSE). "
        f"Context: {context_label}.\n"
        "Use ONLY these real, already-computed numbers -- never invent or assume any "
        f"number not listed here:\n{data}\n\n"
        f"{style}\nEnd with a short reminder that this is educational, not financial advice."
    )


def _gemini_panel_for_mode(context_label: str, data: dict, mode: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(MODEL_NAME)
    response = model.generate_content(_build_prompt(context_label, data, mode))
    text = (response.text or "").strip()
    if not text:
        raise ValueError("Gemini returned an empty AI panel response")
    return text


def generate_ai_panel(context_label: str, data: dict, fallback_text: str, mode: str) -> tuple[str, bool]:
    """Returns (narrative, used_gemini).

    `fallback_text` must already be a complete, mode-appropriate summary (callers build
    this by stitching together `core.explain` outputs for the same data) -- used verbatim
    whenever Gemini is unavailable or fails, so the panel is never blank.
    """
    if GEMINI_API_KEY:
        try:
            return _gemini_panel_for_mode(context_label, data, mode), True
        except Exception as exc:  # Gemini errors must never break the page
            logger.warning("Gemini AI panel failed for %r, falling back to rule-based: %s", context_label, exc)
    return fallback_text, False
