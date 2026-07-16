# Connection-level diagnostics (Step 10)

- WebSocket open time: 2026-07-16T11:16:17.169249+00:00
- TLS/socket info at on_open (from `ws.sock.sock`): {"remote_addr": ["64:ff9b::8d89:e810", 443, 0, 0], "tls_version": "TLSv1.3", "cipher": ["TLS_AES_256_GCM_SHA384", "TLSv1.3", 256]}
- Close code (from the real websocket-client on_close callback, before neo_api_client discards it): None
- Close reason: None
- Ping/pong: not exposed anywhere in this run -- neo_api_client's StartServer registers on_open/on_message/on_error/on_close only (confirmed by source read); no on_ping/on_pong hook exists in its WebSocketApp construction, so no ping/pong timing is available without further, deeper patching that wasn't in scope for this run. Stated here rather than fabricated.
- Total raw frames sent: 5
- Total raw frames received: 6
- Total sdk_on_message invocations (parsed callback level): 1