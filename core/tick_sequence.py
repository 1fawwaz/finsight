"""Live broker migration: tick sequence integrity guard.

Every live tick from any `BrokerAdapter` (see `core/broker_adapter.py`) passes through
one `TickSequenceGuard` instance per symbol stream before touching a cache, classifying
it as `ACCEPT` / `DUPLICATE` / `GAP` / `OUT_OF_ORDER`.

Neither broker integrated in this codebase is confirmed to expose a true exchange-side
packet sequence number: Kotak Neo's raw tick payload (`core/kotak_market_data.py`'s
`_TICK_KEY_MAPPING`, confirmed by reading the installed SDK directly) has no sequence
field at all -- just `ltt`/`v`/`ltp`/`tk`/etc. So this guard's ordering signal is
`exchange_ts` (the broker-reported tick timestamp) by default, with a real
`sequence_id` used instead only when a specific adapter confirms its broker payload
actually provides one, passed in per call -- never assumed or fabricated.

Bounded state: a fixed-size seen-window per symbol for duplicate detection (never grows
unboundedly -- the guard must never become its own memory leak).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Hashable

from core.config import get_logger

logger = get_logger(__name__)

DEFAULT_SEEN_WINDOW_SIZE = 500


class TickOutcome(str, Enum):
    ACCEPT = "ACCEPT"
    DUPLICATE = "DUPLICATE"
    GAP = "GAP"
    OUT_OF_ORDER = "OUT_OF_ORDER"


@dataclass
class TickDecision:
    outcome: TickOutcome
    gap_size: int | None = None  # set only for GAP -- number of missing sequence_ids


def _record_seen(seen_set: set[Hashable], window: deque[Hashable], key: Hashable) -> None:
    """Append `key` to the bounded window, evicting (and un-tracking) the oldest entry
    if the window is already full -- keeps `seen_set` exactly in sync with `window`'s
    actual bounded contents, so the guard's memory never grows past `window.maxlen`."""
    if window.maxlen is not None and len(window) == window.maxlen:
        seen_set.discard(window[0])
    window.append(key)
    seen_set.add(key)


class TickSequenceGuard:
    """Per-symbol duplicate/gap/out-of-order classification for a live tick stream.
    One instance covers every symbol it's called with (internal state is a dict keyed
    by symbol) -- share one guard per adapter, not one per symbol, so counters/state
    stay centrally inspectable via `.counters`.
    """

    def __init__(self, seen_window_size: int = DEFAULT_SEEN_WINDOW_SIZE) -> None:
        self._seen_window_size = seen_window_size
        self._last_exchange_ts: dict[str, datetime] = {}
        self._last_sequence_id: dict[str, int] = {}
        self._seen_sequence_ids: dict[str, set[int]] = {}
        self._seen_sequence_window: dict[str, deque[int]] = {}
        self._seen_timestamps: dict[str, set[datetime]] = {}
        self._seen_timestamps_window: dict[str, deque[datetime]] = {}
        self.counters: dict[str, int] = {"accept": 0, "duplicate": 0, "gap": 0, "out_of_order": 0}

    def evaluate(self, symbol: str, exchange_ts: datetime, sequence_id: int | None = None) -> TickDecision:
        symbol = symbol.strip().upper()
        decision = (
            self._evaluate_with_sequence(symbol, exchange_ts, sequence_id)
            if sequence_id is not None
            else self._evaluate_timestamp_only(symbol, exchange_ts)
        )
        self.counters[decision.outcome.value.lower()] += 1
        if decision.outcome == TickOutcome.GAP:
            logger.warning("tick_sequence_gap symbol=%s gap_size=%d", symbol, decision.gap_size)
        elif decision.outcome == TickOutcome.DUPLICATE:
            logger.info("tick_sequence_duplicate symbol=%s", symbol)
        elif decision.outcome == TickOutcome.OUT_OF_ORDER:
            logger.info("tick_sequence_out_of_order symbol=%s", symbol)
        return decision

    def _evaluate_with_sequence(self, symbol: str, exchange_ts: datetime, sequence_id: int) -> TickDecision:
        seen = self._seen_sequence_ids.setdefault(symbol, set())
        window = self._seen_sequence_window.setdefault(symbol, deque(maxlen=self._seen_window_size))
        if sequence_id in seen:
            return TickDecision(TickOutcome.DUPLICATE)

        last_seq = self._last_sequence_id.get(symbol)
        last_ts = self._last_exchange_ts.get(symbol)
        _record_seen(seen, window, sequence_id)

        if last_seq is None or sequence_id == last_seq + 1:
            self._last_sequence_id[symbol] = sequence_id
            self._last_exchange_ts[symbol] = exchange_ts
            return TickDecision(TickOutcome.ACCEPT)

        if sequence_id > last_seq + 1:
            gap_size = sequence_id - last_seq - 1
            self._last_sequence_id[symbol] = sequence_id
            self._last_exchange_ts[symbol] = exchange_ts
            return TickDecision(TickOutcome.GAP, gap_size=gap_size)

        # sequence_id < last_seq: out of order by sequence. Still applied -- but only
        # to exchange_ts, never regressing the high-water-mark last_sequence_id -- if
        # it doesn't overwrite newer state (compare-and-swap on exchange_ts).
        if last_ts is None or exchange_ts >= last_ts:
            self._last_exchange_ts[symbol] = exchange_ts
            return TickDecision(TickOutcome.ACCEPT)
        return TickDecision(TickOutcome.OUT_OF_ORDER)

    def _evaluate_timestamp_only(self, symbol: str, exchange_ts: datetime) -> TickDecision:
        seen = self._seen_timestamps.setdefault(symbol, set())
        window = self._seen_timestamps_window.setdefault(symbol, deque(maxlen=self._seen_window_size))
        if exchange_ts in seen:
            return TickDecision(TickOutcome.DUPLICATE)
        _record_seen(seen, window, exchange_ts)

        last_ts = self._last_exchange_ts.get(symbol)
        if last_ts is None or exchange_ts > last_ts:
            self._last_exchange_ts[symbol] = exchange_ts
            return TickDecision(TickOutcome.ACCEPT)
        # exchange_ts <= last_ts and not previously seen -- a genuinely stale tick.
        # No sequence_id means "gap size" can't be computed (no counter to diff), so
        # this is classified OUT_OF_ORDER rather than GAP -- an honest limitation of
        # timestamp-only ordering, not a missing feature.
        return TickDecision(TickOutcome.OUT_OF_ORDER)

    def reset(self, symbol: str | None = None) -> None:
        """Clear tracked state -- for a specific symbol, or every symbol if omitted.
        Intended for use after a resubscribe/reconnect where prior sequence state is no
        longer meaningful (a fresh WebSocket session may restart its own tick
        numbering)."""
        if symbol is None:
            self._last_exchange_ts.clear()
            self._last_sequence_id.clear()
            self._seen_sequence_ids.clear()
            self._seen_sequence_window.clear()
            self._seen_timestamps.clear()
            self._seen_timestamps_window.clear()
            return
        symbol = symbol.strip().upper()
        self._last_exchange_ts.pop(symbol, None)
        self._last_sequence_id.pop(symbol, None)
        self._seen_sequence_ids.pop(symbol, None)
        self._seen_sequence_window.pop(symbol, None)
        self._seen_timestamps.pop(symbol, None)
        self._seen_timestamps_window.pop(symbol, None)
