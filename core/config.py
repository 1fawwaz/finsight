"""Central configuration and logging setup for FinSight."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'finsight.db').as_posix()}")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# Every Gemini call site passes this as its request timeout. Measured Gemini latency can
# exceed the Phase 0 "AI response under 8s" budget on a slow connection; without a
# client-side timeout, google-generativeai blocks indefinitely and the UI just hangs on
# a spinner instead of falling back to the always-available rule-based path.
GEMINI_TIMEOUT_SECONDS = 8

DEFAULT_TICKERS: list[str] = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "BHARTIARTL.NS",
    "SBIN.NS",
    "ITC.NS",
    "LT.NS",
    "TMPV.NS",  # Tata Motors Passenger Vehicles Ltd. — successor to TATAMOTORS.NS after its 2025 demerger
]

SUPPORTED_SUFFIXES: tuple[str, ...] = (".NS", ".BO")

BENCHMARK_NIFTY50 = "^NSEI"
BENCHMARK_SENSEX = "^BSESN"
BENCHMARK_BANKNIFTY = "^NSEBANK"
BENCHMARKS: dict[str, str] = {"Nifty 50": BENCHMARK_NIFTY50, "Sensex": BENCHMARK_SENSEX}
# Bank Nifty isn't offered as a portfolio benchmark (BENCHMARKS above), only shown as a
# market-status indicator on the home dashboard -- hence the separate allowlist entry.
_ALLOWED_NON_SUFFIX_SYMBOLS = {BENCHMARK_NIFTY50, BENCHMARK_SENSEX, BENCHMARK_BANKNIFTY}

HISTORY_PERIOD = "5y"

UNSUPPORTED_MARKET_MESSAGE = "Only Indian stocks (NSE/BSE) are supported."


def is_supported_symbol(symbol: str) -> bool:
    """True if `symbol` is an NSE/BSE-suffixed ticker or a recognized benchmark index."""
    symbol = symbol.upper().strip()
    return symbol in _ALLOWED_NON_SUFFIX_SYMBOLS or symbol.endswith(SUPPORTED_SUFFIXES)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger configured with a consistent format."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
