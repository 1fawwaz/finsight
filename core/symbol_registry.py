"""Phase 1 Symbol Registry: permanent, ticker-change-safe identity.

A ticker symbol is not identity -- a company that renames or re-lists under a new ticker
is still the same company, and its history must not fracture across the rename. This
module resolves every symbol to a permanent `internal_id` and is the only place that
creates one.

Reuses `core.universe.resolve_symbol` for name/bare-ticker -> canonical-`.NS`/`.BO`
resolution (not reimplemented here) and follows the same sequential-ID + real
`INSERT ... ON CONFLICT DO NOTHING` pattern already used by
`core.data_ingestion.get_or_create_ticker` and `core.ml.registry._next_model_version`,
for the same reason: two concurrent first-time registrations of the same new symbol must
not raise an unhandled IntegrityError.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from core.config import get_logger
from core.database import Price, SymbolRegistry, Ticker
from core.universe import resolve_symbol

logger = get_logger(__name__)


class AmbiguousRenameError(Exception):
    """Raised when an incoming symbol cannot be safely auto-resolved as either a
    genuinely new listing or a rename of an existing entity -- per the operating spec's
    Hard Stop rule (§11): "pause and present evidence rather than guess." Ticker-string
    similarity alone is not sufficient evidence to auto-merge two identities; an actual
    rename must be recorded explicitly via `record_rename`.
    """


def _next_internal_id(session) -> str:
    """Sequential `FIN-%04d` identifier -- same generation pattern as
    `core.ml.registry._next_model_version` (`f"{name}_v{count+1}"`) and
    `core.ml.data_layer._next_version_name` (`f"dataset_v{count+1}"`), applied here
    instead of inventing a new numbering scheme."""
    count = len(session.execute(select(SymbolRegistry)).scalars().all())
    return f"FIN-{count + 1:04d}"


def _find_by_historical_symbol(session, symbol: str) -> SymbolRegistry | None:
    """Search every registry entry's `historical_symbols_json` for `symbol`. Returns the
    single match, or None. Raises AmbiguousRenameError if more than one entry claims the
    same historical symbol -- that would itself be a data-integrity bug (a symbol string
    was reused by two different companies over time and both were incorrectly recorded
    as the same lineage), not something to silently pick one of.
    """
    all_entries = session.execute(select(SymbolRegistry)).scalars().all()
    matches = []
    for e in all_entries:
        historical = json.loads(e.historical_symbols_json)
        # Entries are {"symbol": ..., "effective_to": ...} dicts (written by
        # record_rename); tolerate bare strings too, since that's the simplest way an
        # integrity-bug test (or a future manual DB correction) could represent the
        # same data.
        symbols_in_history = {h["symbol"] if isinstance(h, dict) else h for h in historical}
        if symbol in symbols_in_history:
            matches.append(e)
    if len(matches) > 1:
        raise AmbiguousRenameError(
            f"Symbol {symbol!r} appears in historical_symbols for multiple internal_ids "
            f"({[m.internal_id for m in matches]}) -- cannot safely resolve which lineage "
            f"it belongs to. This must be corrected by hand, not guessed."
        )
    return matches[0] if matches else None


def get_or_create(
    session,
    symbol: str,
    listing_date: date | None = None,
) -> SymbolRegistry:
    """Resolve `symbol` (name, bare ticker, or full `.NS`/`.BO` symbol) to its permanent
    `SymbolRegistry` row, creating one only if the symbol has never been seen before --
    neither as a current symbol nor as a recorded historical symbol of an existing entity.

    Does NOT attempt to auto-detect "this new symbol is secretly a renamed existing
    company" from ticker-string similarity alone -- that determination requires an
    explicit corporate-action signal and is made via `record_rename`, not guessed here.
    A brand-new symbol always gets a brand-new `internal_id`; an already-recorded rename
    resolves to its existing lineage via `historical_symbols_json`.
    """
    resolved = resolve_symbol(symbol.strip()) or symbol.strip().upper()

    existing = session.execute(select(SymbolRegistry).where(SymbolRegistry.current_symbol == resolved)).scalar_one_or_none()
    if existing is not None:
        return existing

    historical_match = _find_by_historical_symbol(session, resolved)
    if historical_match is not None:
        return historical_match

    internal_id = _next_internal_id(session)
    session.execute(
        sqlite_insert(SymbolRegistry)
        .values(
            internal_id=internal_id,
            current_symbol=resolved,
            historical_symbols_json="[]",
            listing_date=listing_date,
            rename_history_json="[]",
            merger_history_json="[]",
        )
        .on_conflict_do_nothing(index_elements=["current_symbol"])
    )
    session.flush()
    return session.execute(select(SymbolRegistry).where(SymbolRegistry.current_symbol == resolved)).scalar_one()


def record_rename(session, internal_id: str, new_symbol: str, effective_date: date, reason: str | None = None) -> SymbolRegistry:
    """Record a deliberate, evidenced rename: `new_symbol` becomes `current_symbol`, the
    prior `current_symbol` moves into `historical_symbols_json`, and the change is
    appended to `rename_history_json`. Never creates a new `internal_id` and never
    deletes the entity's prior history -- it appends.
    """
    entry = session.execute(select(SymbolRegistry).where(SymbolRegistry.internal_id == internal_id)).scalar_one_or_none()
    if entry is None:
        raise ValueError(f"No SymbolRegistry entry for internal_id {internal_id!r}.")

    old_symbol = entry.current_symbol
    historical = json.loads(entry.historical_symbols_json)
    historical.append({"symbol": old_symbol, "effective_to": effective_date.isoformat()})
    rename_history = json.loads(entry.rename_history_json)
    rename_history.append(
        {"old": old_symbol, "new": new_symbol, "effective_date": effective_date.isoformat(), "reason": reason}
    )

    entry.current_symbol = new_symbol
    entry.historical_symbols_json = json.dumps(historical)
    entry.rename_history_json = json.dumps(rename_history)
    session.flush()
    logger.info("Recorded rename: %s -> %s (internal_id=%s, reason=%s)", old_symbol, new_symbol, internal_id, reason)
    return entry


def record_merger(
    session,
    internal_id: str,
    counterpart_internal_id: str,
    transaction_type: str,
    effective_date: date,
) -> SymbolRegistry:
    """Record a merger/acquisition/demerger relationship on `internal_id`'s side only --
    call twice (once per side) to record it on both, per spec §7.7's "documented
    relationship... on both sides." Never merges or deletes either internal_id's prior
    history.
    """
    entry = session.execute(select(SymbolRegistry).where(SymbolRegistry.internal_id == internal_id)).scalar_one_or_none()
    if entry is None:
        raise ValueError(f"No SymbolRegistry entry for internal_id {internal_id!r}.")

    merger_history = json.loads(entry.merger_history_json)
    merger_history.append(
        {
            "counterpart_internal_id": counterpart_internal_id,
            "transaction_type": transaction_type,
            "effective_date": effective_date.isoformat(),
        }
    )
    entry.merger_history_json = json.dumps(merger_history)
    session.flush()
    logger.info(
        "Recorded merger on internal_id=%s: %s with %s effective %s",
        internal_id, transaction_type, counterpart_internal_id, effective_date,
    )
    return entry


@dataclass
class BackfillResult:
    created: int
    already_present: int
    total_tickers: int


def backfill_registry_from_tickers(session) -> BackfillResult:
    """Populate the Symbol Registry from every existing `Ticker` row -- required
    retroactively per spec §7.7: "this applies retroactively to any existing data keyed
    by raw symbol, which must be backfilled with internal_id mappings, not left as-is."
    Idempotent: re-running only creates entries for tickers not yet registered.
    """
    tickers = session.execute(select(Ticker)).scalars().all()
    created = 0
    already_present = 0
    for ticker in tickers:
        existing = session.execute(
            select(SymbolRegistry).where(SymbolRegistry.current_symbol == ticker.symbol)
        ).scalar_one_or_none()
        if existing is not None:
            already_present += 1
            continue
        get_or_create(session, ticker.symbol)
        created += 1

    logger.info(
        "Symbol Registry backfill: %d created, %d already present, %d total tickers",
        created, already_present, len(tickers),
    )
    return BackfillResult(created=created, already_present=already_present, total_tickers=len(tickers))


def backfill_price_internal_ids(session) -> int:
    """Stamp `internal_id` onto every existing `Price` row that doesn't have one yet, by
    joining `Price.ticker_id -> Ticker.symbol -> SymbolRegistry.current_symbol`. Required
    retroactively per spec §7.7 (existing data keyed by raw symbol must be backfilled,
    not left as-is) -- `backfill_registry_from_tickers` must have already run so every
    `Ticker` has a corresponding registry entry to join against. Idempotent: only rows
    with `internal_id IS NULL` are touched, so a partial/interrupted run resumes safely
    without re-scanning already-stamped rows. Returns the number of rows updated.
    """
    unstamped = session.execute(select(Price).where(Price.internal_id.is_(None))).scalars().all()
    updated = 0
    skipped_no_registry_entry = 0
    for price in unstamped:
        ticker = session.get(Ticker, price.ticker_id)
        if ticker is None:
            skipped_no_registry_entry += 1
            continue
        registry_entry = session.execute(
            select(SymbolRegistry).where(SymbolRegistry.current_symbol == ticker.symbol)
        ).scalar_one_or_none()
        if registry_entry is None:
            skipped_no_registry_entry += 1
            continue
        price.internal_id = registry_entry.internal_id
        updated += 1
    session.flush()
    if skipped_no_registry_entry:
        logger.warning(
            "Price internal_id backfill: %d row(s) skipped -- no Symbol Registry entry found for their ticker. "
            "Run backfill_registry_from_tickers() first.",
            skipped_no_registry_entry,
        )
    logger.info("Price internal_id backfill: %d row(s) stamped, %d unstamped total scanned", updated, len(unstamped))
    return updated
