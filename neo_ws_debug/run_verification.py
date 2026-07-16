"""Kotak Neo SDK transport verification -- Steps 0-8, 10, 11.

Standalone (no FinSight imports). Intercepts the underlying `websocket-client`
library BELOW neo_api_client's own binary parsing (HSWrapper) and BELOW its
own callback wrapping (StartServer), so what gets logged is the true raw
wire traffic -- not neo_api_client's post-parse view of it. This is
interception method #1 from Step 5 (monkey-patch the underlying
websocket-client handler ahead of the SDK's own handler) -- recorded here
explicitly per the instructions.

Safety: exactly one login, one logout, no retries, no reconnect anywhere in
this script (matches the discipline used throughout this whole
investigation -- this hits a real brokerage account).
"""

from __future__ import annotations

import json
import os
import platform
import ssl as ssl_module
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyotp
import requests
from dotenv import load_dotenv

DEBUG_DIR = Path(__file__).resolve().parent
ENV_PATH = DEBUG_DIR.parent / ".env"

EQUITY_OBSERVE_SECONDS = 60
INDEX_OBSERVE_SECONDS = 120
# quotes()/subscribe() both require REAL numeric instrument_tokens -- confirmed from
# neo_api_client/api/quotes_neo_symbol_api.py: get_quotes() builds its request as
# f"{exchange_segment}|{instrument_token}", i.e. instrument_token must be the
# Scrip Master's numeric pSymbol, never a bare trading-symbol string like
# "RELIANCE-EQ" or an index display name like "Nifty 50". All tokens below are
# resolved live via the official Scrip Master, never hardcoded.
EQUITY_SYMBOLS = ["RELIANCE-EQ", "TCS-EQ", "INFY-EQ"]  # matched against pTrdSymbol, nse_cm
INDEX_SUBSCRIBE_NAMES = ["NIFTY", "BANKNIFTY", "SENSEX"]  # matched against pSymbolName; NIFTY/BANKNIFTY=nse_cm, SENSEX=bse_cm


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class RawFrameRecorder:
    """Captures raw send/recv at the websocket-client level, and the real
    close code/reason the SDK itself discards (see module docstring)."""

    def __init__(self) -> None:
        self.raw_frames_path = DEBUG_DIR / "raw_frames.jsonl"
        self.callbacks_path = DEBUG_DIR / "callbacks.log"
        self._raw_fh = open(self.raw_frames_path, "w", encoding="utf-8")
        self._cb_fh = open(self.callbacks_path, "w", encoding="utf-8")
        self.sent_frames: list[dict] = []
        self.recv_frames: list[dict] = []
        self.callback_events: list[dict] = []
        self.ws_open_time: str | None = None
        self.close_code = None
        self.close_reason = None
        self.tls_info: dict | None = None

    def _write_jsonl(self, fh, obj) -> None:
        fh.write(json.dumps(obj, default=str) + "\n")
        fh.flush()

    def log_raw_send(self, payload, opcode) -> None:
        entry = {"ts": _ts(), "direction": "send", "opcode": opcode, "payload_repr": repr(payload), "payload_len": len(payload) if hasattr(payload, "__len__") else None}
        self.sent_frames.append(entry)
        self._write_jsonl(self._raw_fh, entry)

    def log_raw_recv(self, op_code, data) -> None:
        entry = {"ts": _ts(), "direction": "recv", "opcode": op_code, "data_repr": repr(data), "data_len": len(data) if hasattr(data, "__len__") else None}
        self.recv_frames.append(entry)
        self._write_jsonl(self._raw_fh, entry)

    def log_callback(self, name: str, **kwargs) -> None:
        entry = {"ts": _ts(), "callback": name, **kwargs}
        self.callback_events.append(entry)
        line = f"[{entry['ts']}] {name} {kwargs}"
        print(line, flush=True)
        self._cb_fh.write(line + "\n")
        self._cb_fh.flush()

    def close(self) -> None:
        self._raw_fh.close()
        self._cb_fh.close()


def install_hooks(recorder: RawFrameRecorder):
    """Patch websocket-client (not neo_api_client) so every send/recv and
    every callback firing is captured before/around the SDK's own handling.
    Returns the original methods so they can be restored (not strictly
    necessary for a one-shot script, but keeps intent explicit)."""
    import websocket

    orig_send = websocket.WebSocket.send
    orig_recv_data_frame = websocket.WebSocket.recv_data_frame
    orig_app_init = websocket.WebSocketApp.__init__

    def patched_send(self, payload, opcode=websocket.ABNF.OPCODE_TEXT):
        recorder.log_raw_send(payload, opcode)
        return orig_send(self, payload, opcode)

    def patched_recv_data_frame(self, control_frame=False):
        op_code, frame = orig_recv_data_frame(self, control_frame)
        recorder.log_raw_recv(op_code, frame.data if frame is not None else None)
        return op_code, frame

    def patched_app_init(self, *args, **kwargs):
        orig_on_open = kwargs.get("on_open")
        orig_on_message = kwargs.get("on_message")
        orig_on_error = kwargs.get("on_error")
        orig_on_close = kwargs.get("on_close")

        def wrapped_on_open(ws):
            recorder.ws_open_time = _ts()
            try:
                sock = ws.sock.sock
                peer = sock.getpeername()
                info = {"remote_addr": peer}
                if isinstance(sock, ssl_module.SSLSocket):
                    info["tls_version"] = sock.version()
                    info["cipher"] = sock.cipher()
                recorder.tls_info = info
            except Exception as exc:
                recorder.tls_info = {"error": f"could not introspect socket: {exc}"}
            recorder.log_callback("on_open", tls_info=recorder.tls_info)
            if orig_on_open:
                orig_on_open(ws)

        def wrapped_on_message(ws, message):
            recorder.log_callback("on_message", message_repr=repr(message)[:2000])
            if orig_on_message:
                orig_on_message(ws, message)

        def wrapped_on_error(ws, error):
            recorder.log_callback("on_error", error=str(error))
            if orig_on_error:
                orig_on_error(ws, error)

        def wrapped_on_close(ws, close_status_code, close_msg):
            recorder.close_code = close_status_code
            recorder.close_reason = close_msg
            recorder.log_callback("on_close", close_status_code=close_status_code, close_msg=close_msg)
            if orig_on_close:
                orig_on_close(ws, close_status_code, close_msg)

        if "on_open" in kwargs:
            kwargs["on_open"] = wrapped_on_open
        if "on_message" in kwargs:
            kwargs["on_message"] = wrapped_on_message
        if "on_error" in kwargs:
            kwargs["on_error"] = wrapped_on_error
        if "on_close" in kwargs:
            kwargs["on_close"] = wrapped_on_close
        orig_app_init(self, *args, **kwargs)

    websocket.WebSocket.send = patched_send
    websocket.WebSocket.recv_data_frame = patched_recv_data_frame
    websocket.WebSocketApp.__init__ = patched_app_init

    return {
        "method_used": "Monkey-patched websocket.WebSocket.send / websocket.WebSocket.recv_data_frame "
        "(the underlying websocket-client library's core send/receive, called by neo_api_client's "
        "StartServer before its own HSWrapper binary parsing), plus websocket.WebSocketApp.__init__ "
        "to wrap on_open/on_message/on_error/on_close so the real close_status_code/close_msg "
        "(which neo_api_client's own StartServer.on_close discards -- confirmed by reading its "
        "source) are captured.",
        "orig_send": orig_send,
        "orig_recv_data_frame": orig_recv_data_frame,
        "orig_app_init": orig_app_init,
    }


def write_environment_json():
    try:
        freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True, timeout=30).stdout
    except Exception as exc:
        freeze = f"<pip freeze failed: {exc}>"

    import websocket as ws_pkg

    now_utc = datetime.now(timezone.utc)
    payload = {
        "captured_at_utc": now_utc.isoformat(),
        "python_version": sys.version,
        "platform": platform.platform(),
        "os": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
        "websocket_client_version": getattr(ws_pkg, "__version__", "unknown"),
        "env_var_names_present": [k for k in os.environ.keys() if k.startswith("KOTAK_")],
        "pip_freeze": freeze.splitlines(),
    }
    (DEBUG_DIR / "environment.json").write_text(json.dumps(payload, indent=2))
    return payload


class ScripResolver:
    """Resolves both equity (pTrdSymbol exact match) and index (pSymbolName
    exact match) symbols to real numeric instrument_tokens via the official
    Scrip Master, caching each exchange segment's CSV so it's only downloaded
    once per run regardless of how many symbols are resolved against it."""

    def __init__(self, client, evidence_log) -> None:
        self._client = client
        self._log = evidence_log
        self._frames: dict[str, pd.DataFrame] = {}

    def _get_frame(self, segment: str) -> pd.DataFrame:
        import io

        if segment not in self._frames:
            url_or_error = self._client.scrip_master(exchange_segment=segment)
            if isinstance(url_or_error, dict):
                raise RuntimeError(f"scrip_master({segment!r}) failed: {url_or_error}")
            response = requests.get(url_or_error, timeout=30)
            response.raise_for_status()
            df = pd.read_csv(io.StringIO(response.text), low_memory=False)
            df = df.rename(columns=lambda c: c.strip())
            self._frames[segment] = df
        return self._frames[segment]

    def resolve_equity(self, trd_symbol: str, segment: str = "nse_cm") -> dict:
        df = self._get_frame(segment)
        matches = df[df["pTrdSymbol"].astype(str).str.upper().str.strip() == trd_symbol.upper()]
        if matches.empty:
            raise RuntimeError(f"No instrument_token for {trd_symbol!r} (pTrdSymbol) in {segment!r}")
        token = str(matches.iloc[0]["pSymbol"])
        result = {"instrument_token": token, "exchange_segment": segment}
        self._log(f"symbol_resolved symbol={trd_symbol} instrument_token={token} exchange_segment={segment}")
        return result

    def resolve_index(self, symbol_name: str, segment: str) -> dict:
        df = self._get_frame(segment)
        names = df["pSymbolName"].astype(str).str.upper().str.strip()
        matches = df[names == symbol_name.upper()]
        if matches.empty:
            raise RuntimeError(f"No instrument_token for {symbol_name!r} (pSymbolName) in {segment!r}")
        token = str(matches.iloc[0]["pSymbol"])
        result = {"instrument_token": token, "exchange_segment": segment}
        self._log(f"symbol_resolved symbol={symbol_name} instrument_token={token} exchange_segment={segment}")
        return result


def main() -> int:
    load_dotenv(ENV_PATH)
    consumer_key = os.getenv("KOTAK_CONSUMER_KEY", "").strip()
    mobile_number = os.getenv("KOTAK_MOBILE_NUMBER", "").strip()
    ucc = os.getenv("KOTAK_UCC", "").strip()
    mpin = os.getenv("KOTAK_MPIN", "").strip()
    totp_secret = os.getenv("KOTAK_TOTP_SECRET", "").strip()
    environment = os.getenv("KOTAK_ENVIRONMENT", "prod").strip().lower()

    print(f"[{_ts()}] Step 0: environment.json")
    write_environment_json()

    recorder = RawFrameRecorder()
    hook_info = install_hooks(recorder)
    (DEBUG_DIR / "interception_method.json").write_text(json.dumps({"method_used": hook_info["method_used"]}, indent=2))
    print(f"[{_ts()}] Step 5 interception method: {hook_info['method_used']}")

    from neo_api_client import NeoAPI

    client = None
    step_log: list[str] = []

    def log(msg: str) -> None:
        entry = f"[{_ts()}] {msg}"
        print(entry, flush=True)
        step_log.append(entry)

    quotes_results: dict = {}
    correlation_report: dict = {}
    subscription_trace: list[dict] = []
    auth_success = False

    try:
        # ---- Step 1: Authentication (exactly once) ----
        log("Step 1: totp_login request")
        client = NeoAPI(consumer_key=consumer_key, environment=environment)
        client.on_message = lambda m: None  # replaced properly below, after callbacks exist
        client.on_error = lambda e: None
        client.on_open = lambda m="": None
        client.on_close = lambda m="": None

        t0 = time.monotonic()
        totp_code = pyotp.TOTP(totp_secret).now()
        login_resp = client.totp_login(mobile_number=mobile_number, ucc=ucc, totp=totp_code)
        login_latency = time.monotonic() - t0
        login_ok = isinstance(login_resp, dict) and login_resp.get("data") is not None
        log(f"totp_login_result ok={login_ok} latency_s={login_latency:.2f} session_token_returned={bool(login_ok)}")
        if not login_ok:
            log(f"STOPPING: auth failed at totp_login. response={login_resp}")
            return 1

        t0 = time.monotonic()
        validate_resp = client.totp_validate(mpin=mpin)
        validate_latency = time.monotonic() - t0
        validate_ok = isinstance(validate_resp, dict) and validate_resp.get("data") is not None
        log(f"totp_validate_result ok={validate_ok} latency_s={validate_latency:.2f} session_token_returned={bool(validate_ok)}")
        if not validate_ok:
            log(f"STOPPING: auth failed at totp_validate. response={validate_resp}")
            return 1

        auth_success = True
        log("kotak_auth_success -- single authentication attempt for this entire run")

        # ---- Resolve every symbol needed for the whole run, once, via the
        # official Scrip Master (never hardcoded tokens) ----
        resolver = ScripResolver(client, log)
        equity_tokens = {sym: resolver.resolve_equity(sym, "nse_cm") for sym in EQUITY_SYMBOLS}
        index_tokens = {
            "NIFTY": resolver.resolve_index("NIFTY", "nse_cm"),
            "BANKNIFTY": resolver.resolve_index("BANKNIFTY", "nse_cm"),
            "SENSEX": resolver.resolve_index("SENSEX", "bse_cm"),
        }

        # ---- Step 2: REST quotes ----
        log("Step 2: REST quotes for equities + indices")
        quotes_results["equities"] = {}
        for sym, tok in equity_tokens.items():
            t0 = time.monotonic()
            req = {"instrument_tokens": [tok], "quote_type": "ltp"}
            try:
                resp = client.quotes(instrument_tokens=[tok], quote_type="ltp")
                latency = time.monotonic() - t0
                quotes_results["equities"][sym] = {"request": req, "response": resp, "latency_s": latency}
                log(f"quotes_equity symbol={sym} token={tok['instrument_token']} latency_s={latency:.2f}")
            except Exception as exc:
                quotes_results["equities"][sym] = {"request": req, "error": str(exc)}
                log(f"quotes_equity symbol={sym} FAILED error={exc}")

        quotes_results["indices"] = {}
        for name, tok in index_tokens.items():
            t0 = time.monotonic()
            req = {"instrument_tokens": [tok], "quote_type": "ltp"}
            try:
                resp = client.quotes(instrument_tokens=[tok], quote_type="ltp")
                latency = time.monotonic() - t0
                quotes_results["indices"][name] = {"request": req, "response": resp, "latency_s": latency}
                log(f"quotes_index name={name} token={tok['instrument_token']} latency_s={latency:.2f}")
            except Exception as exc:
                quotes_results["indices"][name] = {"request": req, "error": str(exc)}
                log(f"quotes_index name={name} FAILED error={exc}")

        (DEBUG_DIR / "quotes_results.json").write_text(json.dumps(quotes_results, indent=2, default=str))

        # ---- Step 3: REST vs WebSocket correlation (RELIANCE) ----
        log("Step 3: fresh REST quote for RELIANCE immediately before opening the websocket")
        reliance_tok = equity_tokens["RELIANCE-EQ"]
        pre_ws_quote = None
        try:
            pre_ws_quote = client.quotes(instrument_tokens=[reliance_tok], quote_type="ltp")
        except Exception as exc:
            pre_ws_quote = {"error": str(exc)}
        correlation_report["rest_quote_before_ws"] = {"response": pre_ws_quote, "captured_at": _ts()}
        log(f"rest_quote_before_ws={pre_ws_quote}")

        # ---- Step 4: register real callbacks ----
        def on_message(message):
            recorder.log_callback("sdk_on_message", parsed=repr(message)[:2000])

        def on_error(error):
            recorder.log_callback("sdk_on_error", error=str(error))

        def on_open(message=""):
            recorder.log_callback("sdk_on_open", message=message)

        def on_close(message=""):
            recorder.log_callback("sdk_on_close", message=message)

        client.on_message = on_message
        client.on_error = on_error
        client.on_open = on_open
        client.on_close = on_close
        log("Step 4: callbacks (on_open/on_message/on_error/on_close) registered")

        # ---- Step 6: equity stream test (reusing the token already resolved above) ----
        equity_tok = equity_tokens["RELIANCE-EQ"]
        log(f"Step 6: subscribing RELIANCE-EQ (token={equity_tok['instrument_token']}), observing {EQUITY_OBSERVE_SECONDS}s")
        equity_payload = [equity_tok]
        n_recv_before = len(recorder.recv_frames)
        n_sent_before = len(recorder.sent_frames)
        t_sub = _ts()
        ack = client.subscribe(instrument_tokens=equity_payload, isIndex=False)
        subscription_trace.append({
            "step": 6, "label": "equity_RELIANCE", "timestamp": t_sub,
            "python_payload": {"instrument_tokens": equity_payload, "isIndex": False},
            "ack_immediate_return": repr(ack),
        })

        equity_frames_seen = 0
        deadline = time.monotonic() + EQUITY_OBSERVE_SECONDS
        first_equity_frame_saved = False
        while time.monotonic() < deadline:
            new_recv = recorder.recv_frames[n_recv_before:]
            if len(new_recv) > equity_frames_seen:
                equity_frames_seen = len(new_recv)
                if not first_equity_frame_saved and new_recv:
                    (DEBUG_DIR / "first_equity_frame.json").write_text(json.dumps(new_recv[0], indent=2, default=str))
                    first_equity_frame_saved = True
                    log(f"first_equity_frame_saved recv_count={equity_frames_seen}")
            if recorder.close_code is not None:
                log(f"equity_test_ended_early close_code={recorder.close_code} close_reason={recorder.close_reason}")
                break
            time.sleep(1)
        equity_recv_total = recorder.recv_frames[n_recv_before:]
        equity_sent_total = recorder.sent_frames[n_sent_before:]
        log(f"Step 6 result: sent_frames={len(equity_sent_total)} recv_frames={len(equity_recv_total)} sdk_on_message_calls={sum(1 for e in recorder.callback_events if e['callback']=='sdk_on_message')}")

        subscription_trace[-1]["outbound_wire_frames_captured"] = equity_sent_total
        subscription_trace[-1]["inbound_wire_frames_captured"] = equity_recv_total

        # ---- Step 7: index stream test (only if no disconnect yet; tokens already resolved above) ----
        if recorder.close_code is None:
            index_payload = [index_tokens[s] for s in INDEX_SUBSCRIBE_NAMES]
            log(f"Step 7: subscribing {INDEX_SUBSCRIBE_NAMES} isIndex=True, observing {INDEX_OBSERVE_SECONDS}s")
            n_recv_before = len(recorder.recv_frames)
            n_sent_before = len(recorder.sent_frames)
            t_sub = _ts()
            ack = client.subscribe(instrument_tokens=index_payload, isIndex=True)
            subscription_trace.append({
                "step": 7, "label": "index_NIFTY_BANKNIFTY_SENSEX", "timestamp": t_sub,
                "python_payload": {"instrument_tokens": index_payload, "isIndex": True},
                "ack_immediate_return": repr(ack),
                "documented_example_payload_for_comparison": {
                    "note": "Kotak Neo API docs' subscribe() example: client.subscribe(instrument_tokens=[{'instrument_token': '26000', 'exchange_segment': 'nse_cm'}], isIndex=True) -- same shape as what was actually sent; no field rename/type difference observed in the Python-level call.",
                },
            })

            index_frames_seen = 0
            deadline = time.monotonic() + INDEX_OBSERVE_SECONDS
            first_index_frame_saved = False
            while time.monotonic() < deadline:
                new_recv = recorder.recv_frames[n_recv_before:]
                if len(new_recv) > index_frames_seen:
                    index_frames_seen = len(new_recv)
                    if not first_index_frame_saved and new_recv:
                        (DEBUG_DIR / "first_index_name_frame.json").write_text(json.dumps(new_recv[0], indent=2, default=str))
                        first_index_frame_saved = True
                        log(f"first_index_frame_saved recv_count={index_frames_seen}")
                if recorder.close_code is not None:
                    log(f"index_test_ended_early close_code={recorder.close_code} close_reason={recorder.close_reason}")
                    break
                time.sleep(1)
            index_recv_total = recorder.recv_frames[n_recv_before:]
            index_sent_total = recorder.sent_frames[n_sent_before:]
            log(f"Step 7 result: sent_frames={len(index_sent_total)} recv_frames={len(index_recv_total)}")
            subscription_trace[-1]["outbound_wire_frames_captured"] = index_sent_total
            subscription_trace[-1]["inbound_wire_frames_captured"] = index_recv_total
        else:
            log("Step 7 SKIPPED: connection already closed after Step 6 -- no reconnect attempted, per safety rules")

        (DEBUG_DIR / "subscription_trace.json").write_text(json.dumps(subscription_trace, indent=2, default=str))
        correlation_report["equity_frames_received"] = len(recorder.recv_frames)
        correlation_report["conclusion"] = (
            "REST quotes returned data (see quotes_results.json) while the websocket produced "
            f"{len(recorder.recv_frames)} raw inbound frame(s) total -- see raw_frames.jsonl."
        )
        (DEBUG_DIR / "correlation_report.json").write_text(json.dumps(correlation_report, indent=2, default=str))

    except KeyboardInterrupt:
        step_log.append(f"[{_ts()}] interrupted_by_user")
    finally:
        # ---- Step 11: logout, exactly once ----
        logout_result = None
        if client is not None and auth_success:
            try:
                client.logout()
                logout_result = "succeeded"
            except Exception as exc:
                logout_result = f"failed: {exc}"
        else:
            logout_result = "not_attempted"
        step_log.append(f"[{_ts()}] logout_result={logout_result}")
        print(step_log[-1])

        # ---- Step 10: connection diagnostics ----
        connection_report = [
            "# Connection-level diagnostics (Step 10)",
            "",
            f"- WebSocket open time: {recorder.ws_open_time}",
            f"- TLS/socket info at on_open (from `ws.sock.sock`): {json.dumps(recorder.tls_info, default=str)}",
            f"- Close code (from the real websocket-client on_close callback, before neo_api_client discards it): {recorder.close_code}",
            f"- Close reason: {recorder.close_reason}",
            f"- Ping/pong: not exposed anywhere in this run -- neo_api_client's StartServer registers "
            "on_open/on_message/on_error/on_close only (confirmed by source read); no on_ping/on_pong "
            "hook exists in its WebSocketApp construction, so no ping/pong timing is available without "
            "further, deeper patching that wasn't in scope for this run. Stated here rather than fabricated.",
            f"- Total raw frames sent: {len(recorder.sent_frames)}",
            f"- Total raw frames received: {len(recorder.recv_frames)}",
            f"- Total sdk_on_message invocations (parsed callback level): {sum(1 for e in recorder.callback_events if e['callback'] == 'sdk_on_message')}",
        ]
        (DEBUG_DIR / "connection_report.md").write_text("\n".join(connection_report))

        (DEBUG_DIR / "run_log.txt").write_text("\n".join(step_log))
        recorder.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
