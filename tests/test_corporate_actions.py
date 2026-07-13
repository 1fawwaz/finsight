"""Tests for core.corporate_actions: recorded dividend/split retrieval and
price-move-vs-recorded-action consistency validation."""

from datetime import date

from core.corporate_actions import get_corporate_actions, validate_corporate_action_consistency
from core.database import Price


def _add_price(session, internal_id, day, close, dividend=None, split_ratio=None, ticker_id=1):
    session.add(
        Price(
            ticker_id=ticker_id,
            internal_id=internal_id,
            date=day,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000_000,
            dividend=dividend,
            split_ratio=split_ratio,
        )
    )


def test_get_corporate_actions_returns_only_rows_with_events(db_session):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), 100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), 105.0, dividend=2.5)
    _add_price(db_session, "FIN-0001", date(2024, 1, 3), 52.5, split_ratio=2.0)
    db_session.flush()

    events = get_corporate_actions(db_session, "FIN-0001")

    assert len(events) == 2
    assert events[0].trading_date == date(2024, 1, 2)
    assert events[0].dividend == 2.5
    assert events[1].trading_date == date(2024, 1, 3)
    assert events[1].split_ratio == 2.0


def test_validate_passes_when_large_move_has_recorded_split(db_session):
    """A 2-for-1 split roughly halves price -- a large move, but explained."""
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), 100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), 51.0, split_ratio=2.0)
    db_session.flush()

    report = validate_corporate_action_consistency(db_session, "FIN-0001")

    assert report.passed
    assert report.unexplained_large_moves == []


def test_validate_flags_unexplained_large_move(db_session):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), 100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), 130.0)  # +30% move, no recorded action
    db_session.flush()

    report = validate_corporate_action_consistency(db_session, "FIN-0001")

    assert report.passed is False
    assert report.unexplained_large_moves == [date(2024, 1, 2)]


def test_validate_ignores_moves_below_threshold(db_session):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), 100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), 105.0)  # +5%, unremarkable
    db_session.flush()

    report = validate_corporate_action_consistency(db_session, "FIN-0001")

    assert report.passed
    assert report.unexplained_large_moves == []


def test_validate_dividend_alone_does_not_require_a_large_move(db_session):
    """A dividend doesn't necessarily produce a >15% move -- recording it is enough;
    absence of a big price swing on a dividend day is not itself a failure."""
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), 100.0)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), 99.5, dividend=0.5)
    db_session.flush()

    report = validate_corporate_action_consistency(db_session, "FIN-0001")

    assert report.passed
    assert len(report.recorded_events) == 1


def test_validate_only_considers_rows_for_the_given_internal_id(db_session):
    _add_price(db_session, "FIN-0001", date(2024, 1, 1), 100.0, ticker_id=1)
    _add_price(db_session, "FIN-0001", date(2024, 1, 2), 130.0, ticker_id=1)  # unexplained, belongs to FIN-0001
    _add_price(db_session, "FIN-0002", date(2024, 1, 1), 50.0, ticker_id=2)
    _add_price(db_session, "FIN-0002", date(2024, 1, 2), 51.0, ticker_id=2)  # unremarkable, different symbol
    db_session.flush()

    report = validate_corporate_action_consistency(db_session, "FIN-0002")

    assert report.passed
