"""Live integration test for the Upstox market data service.

This is NOT a unit test -- it authenticates against Upstox's real servers using
your real access token from `.env`, subscribes to real live market data, and
waits for real ticks. It is deliberately excluded from the default, CI-safe
`pytest` run: it `pytest.skip()`s immediately, with a clear message naming exactly
which credential is missing, whenever `.env` doesn't have UPSTOX_ANALYTICS_TOKEN
set -- which is true for every environment except a real developer's own machine
with their own Upstox API access configured. Mirrors `tests/test_kotak_live.py`
exactly, same reasoning.

Run it explicitly and deliberately, one at a time, when you want to verify the
live integration end-to-end:

    RUN_UPSTOX_LIVE_TEST=1 pytest tests/test_upstox_live.py -v -s

Deliberately requires **both** a real credential in `.env` AND the
`RUN_UPSTOX_LIVE_TEST=1` environment variable -- having a credential configured
(needed for the app's own live-data panel to work at all) must NOT be enough by
itself to trigger a real connection every time the full suite runs. This test
must never fire as a side effect of routine `pytest -q` runs; it only runs when a
human deliberately opts in for that one invocation.

This run is also the first real confirmation of the assumptions flagged in
BROKER_ARCHITECTURE.md's "Not yet live-verified" section (ltt as epoch
milliseconds, OHLC interval-list ordering, search_instrument's response shape) --
read the printed tick/status output against those assumptions when this runs.
"""

from __future__ import annotations

import os
import time

import pytest

from core.upstox_market_data import UpstoxAuthError, UpstoxCredentialsError, get_market_data_service

# Nifty 50 first (matches this integration's own INDEX_SYMBOLS scope and Kotak's
# live-test preference), falling back to Reliance if the index isn't resolvable.
_CANDIDATE_SYMBOLS = ["NIFTY 50", "RELIANCE.NS"]

_TICK_WAIT_TIMEOUT_SECONDS = 30
_MIN_TICKS_REQUIRED = 5


def _skip_if_credentials_missing():
    if os.getenv("RUN_UPSTOX_LIVE_TEST") != "1":
        pytest.skip(
            "Skipping live Upstox test -- requires explicit opt-in via "
            "RUN_UPSTOX_LIVE_TEST=1 (not run as part of the routine suite; see this "
            "file's module docstring for why)."
        )
    service = get_market_data_service()
    missing = service._missing_credentials()
    if missing:
        pytest.skip(
            f"Skipping live Upstox test -- missing credential(s) in .env: {', '.join(missing)}. "
            "This is expected in CI and on any machine without real Upstox API access."
        )


def test_upstox_live_authentication_and_tick_stream():
    """Load .env -> configure the SDK with the real access token -> connect ->
    validate session -> subscribe to a real symbol -> receive real live ticks ->
    print at least 5 -> exit successfully (unsubscribe + disconnect), in that order."""
    _skip_if_credentials_missing()

    service = get_market_data_service()

    print("\n[1/6] Credentials loaded from .env -- present, not printed.")

    print("[2/6] Configuring SDK and connecting (bearer token, no login flow)...")
    try:
        service._authenticate()
    except UpstoxCredentialsError as exc:
        pytest.fail(f"Credentials error: {exc}")
    except UpstoxAuthError as exc:
        pytest.fail(
            f"Authentication failed against Upstox's real servers: {exc}. "
            "This is a real rejection from Upstox (expired/invalid access token), not a bug in this "
            "test -- verify UPSTOX_ANALYTICS_TOKEN directly against Upstox's developer portal before "
            "retrying."
        )

    status = service.status()
    assert status["authenticated"] is True, f"Expected authenticated=True, got status={status}"
    print(f"[3/6] Session configured. status={status}")

    subscribed_symbol = None
    last_error = None
    for symbol in _CANDIDATE_SYMBOLS:
        try:
            service.subscribe_multiple([symbol])
            subscribed_symbol = symbol
            break
        except Exception as exc:
            last_error = exc
            print(f"    Could not subscribe to {symbol!r} ({exc}); trying next candidate...")
    if subscribed_symbol is None:
        pytest.fail(f"Could not subscribe to any candidate symbol {_CANDIDATE_SYMBOLS}. Last error: {last_error}")

    print(f"[4/6] Subscribed to {subscribed_symbol!r}. Waiting for live ticks (timeout {_TICK_WAIT_TIMEOUT_SECONDS}s)...")

    seen_tick_snapshots = []
    deadline = time.monotonic() + _TICK_WAIT_TIMEOUT_SECONDS
    last_ltp = None
    while time.monotonic() < deadline and len(seen_tick_snapshots) < _MIN_TICKS_REQUIRED:
        tick = service.get_tick(subscribed_symbol)
        if tick is not None and tick.ltp is not None and tick.ltp != last_ltp:
            seen_tick_snapshots.append((tick.ltp, tick.exchange_ts, tick.timestamp))
            last_ltp = tick.ltp
            print(f"    Tick {len(seen_tick_snapshots)}: LTP={tick.ltp} exchange_ts={tick.exchange_ts} ingest_ts={tick.timestamp.isoformat()}")
        time.sleep(0.5)

    print(f"[5/6] Received {len(seen_tick_snapshots)} tick update(s).")
    if len(seen_tick_snapshots) < _MIN_TICKS_REQUIRED:
        print(
            f"    NOTE: fewer than {_MIN_TICKS_REQUIRED} distinct LTP changes arrived within "
            f"{_TICK_WAIT_TIMEOUT_SECONDS}s. Outside NSE/BSE market hours this is expected -- a quiet "
            "market genuinely produces few or no LTP changes. Not treated as a hard failure for that reason."
        )
    else:
        # This is the first real confirmation of the ltt-epoch-milliseconds
        # assumption flagged in BROKER_ARCHITECTURE.md -- a None exchange_ts here
        # despite real ticks arriving would mean that assumption needs revisiting.
        assert any(exchange_ts is not None for _, exchange_ts, _ in seen_tick_snapshots), (
            "Received real ticks but none had a parsed exchange_ts -- the ltt-epoch-milliseconds "
            "assumption in core/upstox_market_data.py may be wrong; check the raw ltt value logged "
            "by upstox_ltt_parse_failed warnings above."
        )

    print("[6/6] Unsubscribing and disconnecting...")
    service.unsubscribe(subscribed_symbol)
    service.stop()
    print("    Done.")

    assert status["authenticated"] is True
