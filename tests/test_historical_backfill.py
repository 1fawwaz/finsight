"""Tests for core.historical_backfill: full-history ingest, resolved through the Symbol
Registry and checkpointed for safe resumption. Follows tests/test_ingestion.py's existing
pattern of monkeypatching core.data_ingestion.yf.Ticker rather than hitting the network.
"""

import json

import pandas as pd
import pytest

from core.checkpoint import get_checkpoint, mark_completed, start_stage
from core.data_ingestion import get_or_create_ticker
from core.database import Price, SymbolRegistry, get_session
from core.historical_backfill import backfill_symbol, backfill_universe
from core.symbol_registry import get_or_create as registry_get_or_create


def _make_history(n_rows: int, start: str = "2024-01-01") -> pd.DataFrame:
    index = pd.date_range(start, periods=n_rows, freq="D")
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n_rows)],
            "High": [101.0 + i for i in range(n_rows)],
            "Low": [99.0 + i for i in range(n_rows)],
            "Close": [100.5 + i for i in range(n_rows)],
            "Volume": [1_000_000 + i for i in range(n_rows)],
        },
        index=index,
    )


class _FakeYfTicker:
    """Stands in for yfinance.Ticker: .info for metadata, .history() for OHLCV.
    `call_log` records every symbol whose .history() was actually invoked, so tests can
    assert a checkpointed-complete symbol's history fetch was skipped entirely."""

    call_log: list[str] = []

    def __init__(self, symbol: str, history_by_symbol: dict[str, pd.DataFrame] | None = None, fail_symbols: set[str] | None = None):
        self._symbol = symbol
        self._history_by_symbol = history_by_symbol or {}
        self._fail_symbols = fail_symbols or set()

    @property
    def info(self):
        return {"shortName": f"{self._symbol} Test Co.", "sector": "Testing"}

    def history(self, period="max", auto_adjust=False):
        _FakeYfTicker.call_log.append(self._symbol)
        if self._symbol in self._fail_symbols:
            return pd.DataFrame()
        return self._history_by_symbol.get(self._symbol, _make_history(5))


@pytest.fixture(autouse=True)
def _reset_call_log():
    _FakeYfTicker.call_log = []
    yield
    _FakeYfTicker.call_log = []


def test_backfill_symbol_creates_registry_entry_and_ingests(temp_db, monkeypatch):
    monkeypatch.setattr("core.data_ingestion.yf.Ticker", lambda symbol: _FakeYfTicker(symbol))

    internal_id, inserted = backfill_symbol("TESTSYM.NS")

    assert internal_id.startswith("FIN-")
    assert inserted == 5
    with get_session() as session:
        assert session.query(SymbolRegistry).filter_by(internal_id=internal_id).count() == 1
        assert session.query(Price).count() == 5


def test_backfill_symbol_uses_max_period_not_default_five_year(temp_db, monkeypatch):
    seen_periods = []

    def fake_ticker(symbol):
        return type(
            "T",
            (),
            {
                "info": {"shortName": "Test", "sector": "Testing"},
                "history": lambda self, period="5y", auto_adjust=False: (seen_periods.append(period), _make_history(3))[1],
            },
        )()

    monkeypatch.setattr("core.data_ingestion.yf.Ticker", fake_ticker)
    backfill_symbol("TESTSYM.NS")

    assert seen_periods == ["max"]


def test_backfill_universe_checkpoints_each_symbol_completed(temp_db, monkeypatch):
    monkeypatch.setattr("core.data_ingestion.yf.Ticker", lambda symbol: _FakeYfTicker(symbol))

    results = backfill_universe(["AAA.NS", "BBB.NS"])

    assert results == {"AAA.NS": 5, "BBB.NS": 5}
    with get_session() as session:
        state = get_checkpoint(session)
        assert state.current_stage == "historical_backfill"
        completed = json.loads(state.completed_internal_ids_json)
        assert len(completed) == 2


def test_backfill_universe_skips_symbol_already_completed_this_stage(temp_db, monkeypatch):
    monkeypatch.setattr("core.data_ingestion.yf.Ticker", lambda symbol: _FakeYfTicker(symbol))

    with get_session() as session:
        registry_entry = registry_get_or_create(session, "AAA.NS")
        start_stage(session, "historical_backfill")
        mark_completed(session, registry_entry.internal_id)

    backfill_universe(["AAA.NS", "BBB.NS"])

    # AAA.NS was pre-marked complete -- its .history() must never have been called.
    assert "AAA.NS" not in _FakeYfTicker.call_log
    assert "BBB.NS" in _FakeYfTicker.call_log


def test_backfill_universe_marks_failed_symbol_without_stopping_the_batch(temp_db, monkeypatch):
    monkeypatch.setattr(
        "core.data_ingestion.yf.Ticker",
        lambda symbol: _FakeYfTicker(symbol, fail_symbols={"BADSYM.NS"}),
    )

    results = backfill_universe(["GOODSYM.NS", "BADSYM.NS"])

    assert results["GOODSYM.NS"] == 5
    assert results["BADSYM.NS"] == 0
    with get_session() as session:
        state = get_checkpoint(session)
        failed = json.loads(state.failed_internal_ids_json)
        assert len(failed) == 1
        completed = json.loads(state.completed_internal_ids_json)
        assert len(completed) == 1
