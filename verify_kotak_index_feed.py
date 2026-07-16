"""Standalone Kotak Neo live index-feed transport verification.

Purpose: determine whether the "subscription succeeds but zero WebSocket
frames ever arrive" problem observed in FinSight exists independently of the
FinSight application itself -- i.e. whether this is a FinSight bug or a
Kotak/account/network-side transport issue.

Deliberately uses ONLY the official `neo_api_client` package directly. Does
NOT import any FinSight module (`core.*`) -- credentials are read straight
from `.env` (the same file FinSight itself reads), but nothing from
FinSight's own code runs. Nothing in `finsight/` is modified by this script.

Safety (same discipline as the rest of this integration -- this hits a real
brokerage account):
  - Exactly ONE authentication attempt. No retry loop, no reconnect logic.
  - Hard 5-minute observation window, then stops regardless of outcome.
  - Always attempts a clean logout in a `finally` block, even on error/Ctrl-C.

Usage:
    python verify_kotak_index_feed.py

Requires KOTAK_CONSUMER_KEY / KOTAK_MOBILE_NUMBER / KOTAK_UCC / KOTAK_MPIN /
KOTAK_TOTP_SECRET / KOTAK_ENVIRONMENT in `.env` (finsight/.env), exactly like
the main app.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyotp
import requests
from dotenv import load_dotenv

OBSERVATION_WINDOW_SECONDS = 5 * 60
TARGET_SYMBOLS = ["NIFTY", "BANKNIFTY", "SENSEX"]  # official Kotak Scrip Master names, confirmed live
SYMBOL_EXCHANGE_SEGMENT = {"NIFTY": "nse_cm", "BANKNIFTY": "nse_cm", "SENSEX": "bse_cm"}

ENV_PATH = Path(__file__).resolve().parent / ".env"
OUTPUT_DIR = Path(__file__).resolve().parent / "kotak_verification_output"
FIRST_FRAME_PATH = OUTPUT_DIR / "first_raw_frame.json"
EVIDENCE_LOG_PATH = OUTPUT_DIR / "verification_log.txt"


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evidence:
    """Accumulates the exact evidence this run needs to report, and writes it
    to both stdout and a log file as it happens (not reconstructed after the
    fact)."""

    def __init__(self) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(EVIDENCE_LOG_PATH, "w", encoding="utf-8")
        self.auth_success: bool | None = None
        self.subscription_request: dict | None = None
        self.callbacks_registered: list[str] = []
        self.ws_lifecycle: list[str] = []
        self.disconnect_reason: str | None = None
        self.frames_received: int = 0
        self.first_frame_saved = False
        self.logout_result: str | None = None

    def log(self, line: str) -> None:
        entry = f"[{_ts()}] {line}"
        print(entry, flush=True)
        self._fh.write(entry + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def resolve_tokens(client, evidence: Evidence) -> dict[str, dict]:
    """Symbol -> {instrument_token, exchange_segment}, resolved live from the
    official Kotak Scrip Master -- never hardcoded. Same two-step fetch
    (scrip_master() for a CSV URL, then download+parse) confirmed from the
    installed SDK's own neo_api_client/api/scrip_search.py reference code."""
    resolved: dict[str, dict] = {}
    frames: dict[str, pd.DataFrame] = {}
    for symbol in TARGET_SYMBOLS:
        segment = SYMBOL_EXCHANGE_SEGMENT[symbol]
        if segment not in frames:
            evidence.log(f"scrip_master_request exchange_segment={segment}")
            url_or_error = client.scrip_master(exchange_segment=segment)
            if isinstance(url_or_error, dict):
                raise RuntimeError(f"scrip_master({segment!r}) failed: {url_or_error}")
            response = requests.get(url_or_error, timeout=30)
            response.raise_for_status()
            df = pd.read_csv(io.StringIO(response.text), low_memory=False)
            df = df.rename(columns=lambda c: c.strip())
            frames[segment] = df
            evidence.log(f"scrip_master_downloaded exchange_segment={segment} rows={len(df)}")

        df = frames[segment]
        names = df["pSymbolName"].astype(str).str.upper().str.strip()
        matches = df[names == symbol]
        if matches.empty:
            raise RuntimeError(f"No instrument_token found for {symbol!r} in the {segment!r} Scrip Master -- refusing to guess.")
        token = str(matches.iloc[0]["pSymbol"])
        resolved[symbol] = {"instrument_token": token, "exchange_segment": segment}
        evidence.log(f"symbol_resolved symbol={symbol} instrument_token={token} exchange_segment={segment}")
    return resolved


def main() -> int:
    load_dotenv(ENV_PATH)
    consumer_key = os.getenv("KOTAK_CONSUMER_KEY", "").strip()
    mobile_number = os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
    ucc = os.getenv("KOTAK_UCC", "").strip()
    mpin = os.getenv("KOTAK_MPIN", "").strip()
    totp_secret = os.getenv("KOTAK_TOTP_SECRET", "").strip()
    environment = os.getenv("KOTAK_ENVIRONMENT", "prod").strip().lower()

    missing = [
        name
        for name, val in [
            ("KOTAK_CONSUMER_KEY", consumer_key),
            ("KOTAK_MOBILE_NUMBER", mobile_number),
            ("KOTAK_UCC", ucc),
            ("KOTAK_MPIN", mpin),
            ("KOTAK_TOTP_SECRET", totp_secret),
        ]
        if not val
    ]
    if missing:
        print(f"Missing required credential(s) in {ENV_PATH}: {', '.join(missing)}")
        return 2

    from neo_api_client import NeoAPI  # imported here, not at module load, so --help/arg errors don't require the SDK

    evidence = Evidence()
    client = None
    frames_log: list[dict] = []

    def on_message(message):
        evidence.frames_received += 1
        evidence.log(f"kotak_ws_raw_frame #{evidence.frames_received} message={message}")
        frames_log.append({"received_at": _ts(), "message": message})
        if not evidence.first_frame_saved:
            FIRST_FRAME_PATH.write_text(json.dumps({"received_at": _ts(), "raw_message": message}, indent=2, default=str))
            evidence.first_frame_saved = True
            evidence.log(f"first_raw_frame_saved_to={FIRST_FRAME_PATH}")

    def on_error(error):
        evidence.disconnect_reason = f"on_error: {error}"
        evidence.ws_lifecycle.append(f"on_error: {error}")
        evidence.log(f"kotak_ws_error error={error}")

    def on_open(message=""):
        evidence.ws_lifecycle.append(f"on_open: {message}")
        evidence.log(f"kotak_ws_open message={message}")

    def on_close(message=""):
        evidence.disconnect_reason = evidence.disconnect_reason or f"on_close: {message}"
        evidence.ws_lifecycle.append(f"on_close: {message}")
        evidence.log(f"kotak_ws_close message={message}")

    try:
        evidence.log(f"starting_verification environment={environment} ucc={ucc}")

        client = NeoAPI(consumer_key=consumer_key, environment=environment)
        client.on_message = on_message
        client.on_error = on_error
        client.on_open = on_open
        client.on_close = on_close
        evidence.callbacks_registered = ["on_message", "on_error", "on_open", "on_close"]
        evidence.log(f"callbacks_registered={evidence.callbacks_registered}")

        totp_code = pyotp.TOTP(totp_secret).now()
        evidence.log("totp_generated")

        evidence.log("totp_login_request")
        login_resp = client.totp_login(mobile_number=mobile_number, ucc=ucc, totp=totp_code)
        if not isinstance(login_resp, dict) or login_resp.get("data") is None:
            evidence.auth_success = False
            evidence.log(f"totp_login_failed response={login_resp}")
            return 1
        evidence.log("totp_login_succeeded")

        evidence.log("totp_validate_request")
        validate_resp = client.totp_validate(mpin=mpin)
        if not isinstance(validate_resp, dict) or validate_resp.get("data") is None:
            evidence.auth_success = False
            evidence.log(f"totp_validate_failed response={validate_resp}")
            return 1

        evidence.auth_success = True
        evidence.log("kotak_auth_success -- SINGLE authentication attempt for this run, no retries will occur")

        tokens = resolve_tokens(client, evidence)

        payload = [{"instrument_token": tokens[s]["instrument_token"], "exchange_segment": tokens[s]["exchange_segment"]} for s in TARGET_SYMBOLS]
        evidence.subscription_request = {"instrument_tokens": payload, "isIndex": True}
        evidence.log(f"subscription_request symbols={TARGET_SYMBOLS} payload={payload}")
        ack = client.subscribe(instrument_tokens=payload, isIndex=True)
        evidence.log(f"subscription_ack response={ack}")

        evidence.log(f"observing for up to {OBSERVATION_WINDOW_SECONDS}s -- waiting for any WebSocket frame...")
        start = time.monotonic()
        deadline = start + OBSERVATION_WINDOW_SECONDS
        last_status_log = 0.0
        confirm_deadline: float | None = None  # set once the first frame arrives
        while time.monotonic() < deadline:
            if evidence.disconnect_reason is not None:
                evidence.log("stopping_early reason=genuine_disconnect_detected (no reconnect attempted, per this script's safety design)")
                break
            if confirm_deadline is None and evidence.frames_received > 0:
                # First frame arrived -- per this run's conditions, don't wait
                # out the full 5 minutes; observe briefly longer to confirm the
                # feed is genuinely active (not one fluke frame), then exit.
                confirm_deadline = time.monotonic() + 20
                evidence.log("first_frame_arrived -- observing 20 more seconds to confirm the feed is active, then exiting early")
            if confirm_deadline is not None and time.monotonic() >= confirm_deadline:
                evidence.log(f"stopping_early reason=feed_confirmed_active frames_received={evidence.frames_received}")
                break
            elapsed = time.monotonic() - start
            if elapsed - last_status_log >= 30:
                evidence.log(f"still_waiting elapsed_s={elapsed:.0f} frames_received={evidence.frames_received}")
                last_status_log = elapsed
            time.sleep(1)

        evidence.log(f"observation_window_ended frames_received={evidence.frames_received}")

    except KeyboardInterrupt:
        evidence.log("interrupted_by_user")
    finally:
        if client is not None:
            try:
                client.logout()
                evidence.logout_result = "succeeded"
                evidence.log("logout_succeeded")
            except Exception as exc:
                evidence.logout_result = f"failed: {exc}"
                evidence.log(f"logout_failed error={exc}")
        else:
            evidence.logout_result = "not_attempted (never authenticated)"

        # Final conclusion classification, per this run's required categories.
        if evidence.auth_success is not True:
            conclusion = "Authentication failure"
            explanation = "totp_login/totp_validate did not both return a data payload -- see totp_login_failed/totp_validate_failed above."
        elif evidence.frames_received > 0:
            conclusion = "Feed working"
            explanation = f"{evidence.frames_received} raw WebSocket frame(s) received via on_message; first frame saved to {FIRST_FRAME_PATH}."
        elif evidence.disconnect_reason and ("winerror" in evidence.disconnect_reason.lower() or "timed out" in evidence.disconnect_reason.lower() or "timeout" in evidence.disconnect_reason.lower() or "connection" in evidence.disconnect_reason.lower()):
            conclusion = "Network/transport failure"
            explanation = f"WebSocket was authenticated and subscribed but disconnected with a transport-level error before any frame arrived: {evidence.disconnect_reason}"
        elif evidence.disconnect_reason:
            conclusion = "Other"
            explanation = f"Session ended for a reason that isn't clearly network/transport: {evidence.disconnect_reason}"
        else:
            conclusion = "Feed silent"
            explanation = f"Authenticated, subscribed, WebSocket stayed open for the full observation window, but zero frames arrived (no disconnect either) -- the connection was live but nothing was ever pushed."

        evidence.log("--- SUMMARY ---")
        evidence.log(f"auth_success={evidence.auth_success}")
        evidence.log(f"callbacks_registered={evidence.callbacks_registered}")
        evidence.log(f"on_message_invoked={evidence.frames_received > 0}")
        evidence.log(f"subscription_request={evidence.subscription_request}")
        evidence.log(f"ws_lifecycle={evidence.ws_lifecycle}")
        evidence.log(f"disconnect_reason={evidence.disconnect_reason}")
        evidence.log(f"frames_received={evidence.frames_received}")
        evidence.log(f"first_raw_frame_json_created={FIRST_FRAME_PATH.exists()}")
        evidence.log(f"logout_result={evidence.logout_result}")
        evidence.log(f"CONCLUSION: {conclusion} -- {explanation}")
        evidence.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
