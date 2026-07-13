"""Tests for core.symbol_registry: permanent, ticker-change-safe identity."""

from datetime import date

import pytest

from core.database import SymbolRegistry, Ticker
from core.symbol_registry import (
    AmbiguousRenameError,
    backfill_registry_from_tickers,
    get_or_create,
    record_merger,
    record_rename,
)


def test_get_or_create_assigns_sequential_internal_id(db_session):
    first = get_or_create(db_session, "RELIANCE.NS")
    second = get_or_create(db_session, "TCS.NS")
    assert first.internal_id == "FIN-0001"
    assert second.internal_id == "FIN-0002"


def test_get_or_create_is_idempotent(db_session):
    first = get_or_create(db_session, "RELIANCE.NS")
    again = get_or_create(db_session, "RELIANCE.NS")
    assert first.internal_id == again.internal_id
    assert db_session.query(SymbolRegistry).count() == 1


def test_get_or_create_resolves_company_name_and_bare_ticker_to_same_entry(db_session):
    by_symbol = get_or_create(db_session, "RELIANCE.NS")
    by_bare = get_or_create(db_session, "RELIANCE")
    assert by_symbol.internal_id == by_bare.internal_id


def test_record_rename_appends_not_replaces(db_session):
    entry = get_or_create(db_session, "OLDSYM.NS")
    internal_id = entry.internal_id

    renamed = record_rename(db_session, internal_id, "NEWSYM.NS", date(2025, 6, 1), reason="test rename")

    assert renamed.internal_id == internal_id
    assert renamed.current_symbol == "NEWSYM.NS"
    assert "OLDSYM.NS" in renamed.historical_symbols_json
    assert "NEWSYM.NS" in renamed.rename_history_json
    assert "test rename" in renamed.rename_history_json


def test_get_or_create_resolves_historical_symbol_to_existing_lineage_after_rename(db_session):
    original = get_or_create(db_session, "OLDSYM.NS")
    record_rename(db_session, original.internal_id, "NEWSYM.NS", date(2025, 6, 1), reason="renamed")

    via_new = get_or_create(db_session, "NEWSYM.NS")
    via_old = get_or_create(db_session, "OLDSYM.NS")

    assert via_new.internal_id == original.internal_id
    assert via_old.internal_id == original.internal_id
    # No new row was created for the retired symbol -- only one registry entry exists.
    assert db_session.query(SymbolRegistry).count() == 1


def test_tatamotors_to_tmpv_real_world_rename_scenario(db_session):
    """Grounded in FinSight's real history: TATAMOTORS.NS was delisted after Tata
    Motors' 2025 demerger, succeeded by TMPV.NS (see docs/DATA_SOURCE.md). This is
    exactly the case the Symbol Registry exists to handle without a one-off manual
    substitution in DEFAULT_TICKERS."""
    original = get_or_create(db_session, "TATAMOTORS.NS")
    record_rename(
        db_session, original.internal_id, "TMPV.NS", date(2025, 1, 1),
        reason="2025 demerger; TMPV.NS is the passenger-vehicle successor",
    )

    resolved_new = get_or_create(db_session, "TMPV.NS")
    resolved_old = get_or_create(db_session, "TATAMOTORS.NS")
    assert resolved_new.internal_id == resolved_old.internal_id == original.internal_id
    assert resolved_new.current_symbol == "TMPV.NS"


def test_record_merger_recorded_without_altering_current_symbol(db_session):
    entry_a = get_or_create(db_session, "COMPA.NS")
    entry_b = get_or_create(db_session, "COMPB.NS")

    updated_a = record_merger(db_session, entry_a.internal_id, entry_b.internal_id, "acquisition", date(2026, 1, 1))

    assert updated_a.current_symbol == "COMPA.NS"  # merger recorded, identity not altered
    assert entry_b.internal_id in updated_a.merger_history_json
    assert "acquisition" in updated_a.merger_history_json


def test_ambiguous_historical_symbol_raises_rather_than_guessing(db_session):
    """Two entries both (incorrectly) claiming the same historical symbol is a
    data-integrity bug that must surface loudly, not resolve silently to either one."""
    entry_a = get_or_create(db_session, "AAA.NS")
    entry_b = get_or_create(db_session, "BBB.NS")
    # Directly corrupt state to simulate the bug condition (this should never happen via
    # the normal record_rename API, which is exactly why it's worth a hard failure).
    entry_a.historical_symbols_json = '["DUPCLAIM.NS"]'
    entry_b.historical_symbols_json = '["DUPCLAIM.NS"]'
    db_session.flush()

    with pytest.raises(AmbiguousRenameError):
        get_or_create(db_session, "DUPCLAIM.NS")


def test_backfill_registry_from_existing_tickers_covers_all(db_session):
    db_session.add_all(
        [
            Ticker(symbol="RELIANCE.NS", name="Reliance Industries", sector="Energy"),
            Ticker(symbol="TCS.NS", name="Tata Consultancy Services", sector="IT"),
            Ticker(symbol="INFY.NS", name="Infosys", sector="IT"),
        ]
    )
    db_session.flush()

    result = backfill_registry_from_tickers(db_session)

    assert result.created == 3
    assert result.already_present == 0
    assert result.total_tickers == 3
    assert db_session.query(SymbolRegistry).count() == 3
    symbols = {r.current_symbol for r in db_session.query(SymbolRegistry).all()}
    assert symbols == {"RELIANCE.NS", "TCS.NS", "INFY.NS"}


def test_backfill_is_idempotent(db_session):
    db_session.add(Ticker(symbol="RELIANCE.NS", name="Reliance Industries", sector="Energy"))
    db_session.flush()

    first = backfill_registry_from_tickers(db_session)
    second = backfill_registry_from_tickers(db_session)

    assert first.created == 1
    assert second.created == 0
    assert second.already_present == 1
    assert db_session.query(SymbolRegistry).count() == 1


def test_current_symbol_uniqueness_enforced_at_db_level(db_session):
    """The `UNIQUE(current_symbol)` constraint is what makes get_or_create's
    ON CONFLICT DO NOTHING pattern safe under a concurrent-insert race, mirroring
    core.data_ingestion.get_or_create_ticker's existing pattern for the same reason."""
    get_or_create(db_session, "RELIANCE.NS")
    # A second, direct raw insert attempting to reuse the same current_symbol must be
    # rejected by the schema itself, not merely by application-level logic.
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        db_session.add(SymbolRegistry(internal_id="FIN-9999", current_symbol="RELIANCE.NS"))
        db_session.flush()
