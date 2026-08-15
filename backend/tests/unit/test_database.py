"""Unit tests for the shared database engine and session factory.

These never open a connection. Constitution IV forbids tests contacting external
systems, and everything asserted here is configuration that is decided at import
time — the transaction behaviour is exercised against a fake session so the
commit/rollback contract is protected without a live PostgreSQL.
"""

import pytest

from app.common.database import (
    Base,
    dispose_engine,
    engine,
    get_session,
    session_factory,
)


class FakeSession:
    """Stands in for AsyncSession, recording which transaction calls were made."""

    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False


def _patch_factory(monkeypatch, session):
    """Replace the module's session factory so get_session yields `session`."""
    monkeypatch.setattr("app.common.database.session_factory", lambda: session)


def test_engine_uses_an_async_driver():
    # Constitution VI forbids blocking I/O on request paths, which a synchronous
    # driver would reintroduce regardless of how the route is declared.
    assert engine.url.drivername == "postgresql+asyncpg"


def test_engine_verifies_pooled_connections_before_use():
    assert engine.pool._pre_ping is True


def test_committed_objects_stay_readable():
    # expire_on_commit=True would make attribute access after commit emit a refresh
    # query, which an async session raises on rather than lazily loading.
    assert session_factory.kw["expire_on_commit"] is False


def test_constraints_are_named_deterministically():
    # Alembic cannot drop a constraint it cannot name, so names must not be left to
    # PostgreSQL to invent.
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
    assert Base.metadata.naming_convention["fk"] == (
        "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
    )


async def test_a_successful_request_commits_once_and_closes(monkeypatch):
    session = FakeSession()
    _patch_factory(monkeypatch, session)

    async for yielded in get_session():
        assert yielded is session

    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True


async def test_a_failing_request_rolls_back_and_propagates(monkeypatch):
    session = FakeSession()
    _patch_factory(monkeypatch, session)

    agen = get_session()
    await agen.asend(None)

    with pytest.raises(RuntimeError, match="service failed"):
        await agen.athrow(RuntimeError("service failed"))

    assert session.rolled_back is True
    assert session.committed is False
    assert session.closed is True


async def test_disposing_the_engine_releases_pooled_connections(monkeypatch):
    disposed = False

    async def fake_dispose():
        nonlocal disposed
        disposed = True

    class FakeEngine:
        dispose = staticmethod(fake_dispose)

    monkeypatch.setattr("app.common.database.engine", FakeEngine)
    await dispose_engine()

    assert disposed is True
