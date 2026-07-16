"""Minimal reproduction: Kotak Neo index-feed silence.

Only the official neo_api_client package. No FinSight code, no helper
modules, no wrappers. Credentials read from .env (KOTAK_CONSUMER_KEY,
KOTAK_MOBILE_NUMBER, KOTAK_UCC, KOTAK_MPIN, KOTAK_TOTP_SECRET,
KOTAK_ENVIRONMENT). One auth, one REST quote, one subscription, a brief
observation window, one logout -- no retries.

Expected failure mode this reproduces: authentication and subscription both
succeed with no error, but on_message is never invoked for the subscribed
index token(s) before the connection times out or is closed.

Run: python minimal_repro.py
"""

import os
import time

import pyotp
from dotenv import load_dotenv
from neo_api_client import NeoAPI

load_dotenv(".env")

client = NeoAPI(
    consumer_key=os.getenv("KOTAK_CONSUMER_KEY"),
    environment=os.getenv("KOTAK_ENVIRONMENT", "prod"),
)

frames_received = []
client.on_message = lambda msg: frames_received.append(msg)
client.on_error = lambda err: print("on_error:", err)
client.on_open = lambda msg="": print("on_open:", msg)
client.on_close = lambda msg="": print("on_close:", msg)

# --- one authentication attempt ---
totp = pyotp.TOTP(os.getenv("KOTAK_TOTP_SECRET")).now()
login = client.totp_login(
    mobile_number=os.getenv("KOTAK_MOBILE_NUMBER"),
    ucc=os.getenv("KOTAK_UCC"),
    totp=totp,
)
assert isinstance(login, dict) and login.get("data") is not None, f"login failed: {login}"

validate = client.totp_validate(mpin=os.getenv("KOTAK_MPIN"))
assert isinstance(validate, dict) and validate.get("data") is not None, f"validate failed: {validate}"
print("Authenticated.")

# --- one REST quote, to show the market-data backend itself works ---
# NIFTY's real instrument_token (nse_cm segment), resolved once via the Scrip
# Master in earlier runs of this investigation -- reused here as a known-good
# constant since this script's purpose is reproduction, not re-resolution.
NIFTY_TOKEN = {"instrument_token": "26000", "exchange_segment": "nse_cm"}

quote = client.quotes(instrument_tokens=[NIFTY_TOKEN], quote_type="ltp")
print("REST quote for NIFTY:", quote)

# --- one subscription, isIndex=True ---
print("Subscribing to NIFTY (isIndex=True)...")
client.subscribe(instrument_tokens=[NIFTY_TOKEN], isIndex=True)

# --- brief observation window ---
OBSERVE_SECONDS = 90
deadline = time.monotonic() + OBSERVE_SECONDS
while time.monotonic() < deadline:
    if frames_received:
        print(f"Received {len(frames_received)} frame(s). First: {frames_received[0]}")
        break
    time.sleep(1)
else:
    print(f"No frames received after {OBSERVE_SECONDS}s despite successful auth + subscription.")

# --- logout, exactly once, no reconnect ---
try:
    client.logout()
    print("Logged out.")
except Exception as exc:
    print("Logout failed:", exc)
