"""Unit tests for the FastAPI application setup.

Nothing here opens a connection. The lifespan test replaces the pool-closing functions
with recording stand-ins, so shutdown is verified without a live PostgreSQL or Redis.
"""

import app.main as main_module
from app.common.exceptions import NotFoundError
from app.main import app, create_app


def test_the_module_exposes_an_app_for_uvicorn():
    # quickstart.md runs `uvicorn app.main:app`; renaming this breaks the documented
    # way to start the server.
    from fastapi import FastAPI

    assert isinstance(app, FastAPI)


def test_each_call_builds_an_independent_application():
    # Tests need an isolated instance; sharing one would let a route added by a test
    # leak into every other test.
    assert create_app() is not create_app()


def test_the_application_is_named_and_versioned():
    built = create_app()

    assert built.title == "JammySearch"
    assert built.version


def test_error_handling_is_installed():
    # Proven by behaviour rather than by inspecting the handler registry: a typed
    # exception must come back as the shared envelope.
    from fastapi.testclient import TestClient

    built = create_app()

    @built.get("/probe")
    async def probe():
        raise NotFoundError("That audience no longer exists.")

    response = TestClient(built).get("/probe")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_only_contracted_endpoints_are_exposed():
    # contracts/rest-api.md is the single source of truth for the HTTP surface
    # (Constitution III). Nothing may appear here ahead of the task that contracts it —
    # including a convenience health check. `/auth/session` is contracted under "Sessions".
    # Asserted against the generated OpenAPI schema rather than the route table: that schema
    # is what the frontend and the docs are written against, so it is the surface the contract
    # is actually a contract for.
    built = create_app()

    assert set(built.openapi()["paths"]) == {"/auth/session"}


async def test_startup_does_not_connect_to_anything(monkeypatch):
    # Both pools connect lazily. Connecting eagerly would make the server refuse to
    # start whenever a dependency is briefly unavailable.
    calls = []
    monkeypatch.setattr(main_module, "dispose_engine", _recorder(calls, "engine"))
    monkeypatch.setattr(main_module, "close_redis", _recorder(calls, "redis"))

    built = create_app()
    async with built.router.lifespan_context(built):
        assert calls == []


async def test_shutdown_closes_both_pools(monkeypatch):
    calls = []
    monkeypatch.setattr(main_module, "dispose_engine", _recorder(calls, "engine"))
    monkeypatch.setattr(main_module, "close_redis", _recorder(calls, "redis"))

    built = create_app()
    async with built.router.lifespan_context(built):
        pass

    assert calls == ["engine", "redis"]


def _recorder(calls, name):
    async def record():
        calls.append(name)

    return record
