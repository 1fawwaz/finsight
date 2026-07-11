"""Shared pytest fixtures: an isolated in-memory DB session for each test."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from core.database import Base


@pytest.fixture()
def db_session():
    """A fresh in-memory SQLite session, isolated from the real finsight.db."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def temp_db(monkeypatch):
    """Points core.database.get_session() at a throwaway in-memory DB for the duration of a test.

    Useful for exercising functions (core.portfolio CRUD, core.queries) that open their
    own sessions internally rather than accepting one as a parameter.
    """
    engine = create_engine(
        "sqlite:///:memory:", future=True, poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    monkeypatch.setattr("core.database.SessionLocal", session_factory)
    yield
    engine.dispose()
