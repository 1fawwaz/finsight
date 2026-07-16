"""Tests for core/tick_sequence.py -- duplicate/gap/out-of-order tick classification,
in both sequence-id mode and timestamp-only mode (since no broker integrated in this
codebase is confirmed to expose a real packet sequence number)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.tick_sequence import TickOutcome, TickSequenceGuard

T0 = datetime(2026, 7, 16, 9, 15, 0, tzinfo=timezone.utc)


def _t(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


class TestSequenceIdMode:
    def test_first_tick_is_accepted(self):
        guard = TickSequenceGuard()
        decision = guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        assert decision.outcome == TickOutcome.ACCEPT

    def test_consecutive_sequence_ids_are_accepted(self):
        guard = TickSequenceGuard()
        for i in range(1, 6):
            decision = guard.evaluate("RELIANCE.NS", _t(i), sequence_id=i)
            assert decision.outcome == TickOutcome.ACCEPT

    def test_repeated_sequence_id_is_duplicate(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        decision = guard.evaluate("RELIANCE.NS", _t(1), sequence_id=1)
        assert decision.outcome == TickOutcome.DUPLICATE

    def test_skipped_sequence_id_is_a_gap_and_still_processed(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        decision = guard.evaluate("RELIANCE.NS", _t(1), sequence_id=5)
        assert decision.outcome == TickOutcome.GAP
        assert decision.gap_size == 3  # sequence_ids 2, 3, 4 missing

    def test_gap_tick_updates_high_water_mark(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        guard.evaluate("RELIANCE.NS", _t(1), sequence_id=5)
        # the next in-order tick after the gap is sequence_id=6, not 2
        decision = guard.evaluate("RELIANCE.NS", _t(2), sequence_id=6)
        assert decision.outcome == TickOutcome.ACCEPT

    def test_lower_sequence_id_with_older_timestamp_is_out_of_order_and_dropped(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(10), sequence_id=10)
        decision = guard.evaluate("RELIANCE.NS", _t(1), sequence_id=3)  # older ts, lower seq
        assert decision.outcome == TickOutcome.OUT_OF_ORDER

    def test_lower_sequence_id_with_newer_timestamp_is_accepted_via_cas(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(1), sequence_id=10)
        # a lower sequence_id but a genuinely newer exchange_ts must still be applied
        decision = guard.evaluate("RELIANCE.NS", _t(20), sequence_id=3)
        assert decision.outcome == TickOutcome.ACCEPT

    def test_out_of_order_tick_seen_again_is_a_duplicate_not_out_of_order_again(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(10), sequence_id=10)
        guard.evaluate("RELIANCE.NS", _t(1), sequence_id=3)  # dropped, out of order
        decision = guard.evaluate("RELIANCE.NS", _t(1), sequence_id=3)  # replayed
        assert decision.outcome == TickOutcome.DUPLICATE

    def test_symbols_are_tracked_independently(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        decision = guard.evaluate("TCS.NS", _t(0), sequence_id=1)
        assert decision.outcome == TickOutcome.ACCEPT  # not a duplicate of RELIANCE's seq=1


class TestTimestampOnlyMode:
    def test_first_tick_is_accepted(self):
        guard = TickSequenceGuard()
        decision = guard.evaluate("RELIANCE.NS", _t(0))
        assert decision.outcome == TickOutcome.ACCEPT

    def test_strictly_newer_timestamp_is_accepted(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0))
        decision = guard.evaluate("RELIANCE.NS", _t(1))
        assert decision.outcome == TickOutcome.ACCEPT

    def test_identical_timestamp_is_duplicate(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(5))
        decision = guard.evaluate("RELIANCE.NS", _t(5))
        assert decision.outcome == TickOutcome.DUPLICATE

    def test_older_timestamp_is_out_of_order(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(10))
        decision = guard.evaluate("RELIANCE.NS", _t(5))
        assert decision.outcome == TickOutcome.OUT_OF_ORDER

    def test_no_gap_classification_possible_without_a_counter(self):
        # Timestamp-only mode never returns GAP -- there's no counter to diff, so a
        # "gap" can't be sized. This is an explicit, documented limitation.
        guard = TickSequenceGuard()
        for i in range(0, 100, 10):
            decision = guard.evaluate("RELIANCE.NS", _t(i))
            assert decision.outcome != TickOutcome.GAP


class TestBoundedState:
    def test_seen_window_does_not_grow_unbounded(self):
        guard = TickSequenceGuard(seen_window_size=10)
        for i in range(1, 1000):
            guard.evaluate("RELIANCE.NS", _t(i), sequence_id=i)
        assert len(guard._seen_sequence_window["RELIANCE.NS"]) <= 10
        assert len(guard._seen_sequence_ids["RELIANCE.NS"]) <= 10

    def test_evicted_sequence_id_can_be_seen_as_new_again_not_falsely_flagged_duplicate(self):
        # Once a very old sequence_id has been evicted from the bounded window, the
        # guard can no longer distinguish "genuinely new" from "an ancient replay" --
        # this is an accepted, documented tradeoff of bounded memory, not a bug.
        guard = TickSequenceGuard(seen_window_size=3)
        for i in range(1, 10):
            guard.evaluate("RELIANCE.NS", _t(i), sequence_id=i)
        assert 1 not in guard._seen_sequence_ids["RELIANCE.NS"]


class TestCounters:
    def test_counters_track_every_outcome_type(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)  # accept
        guard.evaluate("RELIANCE.NS", _t(1), sequence_id=1)  # duplicate
        guard.evaluate("RELIANCE.NS", _t(2), sequence_id=5)  # gap
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=2)  # out of order (older ts, lower seq than 5)
        assert guard.counters["accept"] == 1  # only the first tick -- GAP has its own counter
        assert guard.counters["duplicate"] == 1
        assert guard.counters["gap"] == 1
        assert guard.counters["out_of_order"] == 1


class TestReset:
    def test_reset_one_symbol_clears_only_that_symbols_state(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        guard.evaluate("TCS.NS", _t(0), sequence_id=1)
        guard.reset("RELIANCE.NS")
        # RELIANCE's sequence=1 is now treated as fresh again (state cleared)
        decision = guard.evaluate("RELIANCE.NS", _t(1), sequence_id=1)
        assert decision.outcome == TickOutcome.ACCEPT
        # TCS state untouched -- its sequence=1 is still remembered as seen
        decision_tcs = guard.evaluate("TCS.NS", _t(1), sequence_id=1)
        assert decision_tcs.outcome == TickOutcome.DUPLICATE

    def test_reset_all_clears_every_symbol(self):
        guard = TickSequenceGuard()
        guard.evaluate("RELIANCE.NS", _t(0), sequence_id=1)
        guard.evaluate("TCS.NS", _t(0), sequence_id=1)
        guard.reset()
        assert guard.evaluate("RELIANCE.NS", _t(1), sequence_id=1).outcome == TickOutcome.ACCEPT
        assert guard.evaluate("TCS.NS", _t(1), sequence_id=1).outcome == TickOutcome.ACCEPT
