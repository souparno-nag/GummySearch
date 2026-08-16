"""Unit tests for the shared FastAPI dependencies (FR-048, FR-079).

`current_user` is the gate every authenticated route sits behind, so the properties worth
asserting are the refusals: no cookie, an unknown token, an expired session. They are
exercised through a real request rather than by calling the function directly, because the
thing being tested is the wiring — a dependency that works in isolation but is not attached
to the cookie the client actually sends protects nothing.

Redis is the in-memory fake from `tests/conftest.py`; no test contacts a real service.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.users.auth_service as auth
from app.common.middleware import install_error_handling
from app.common.redis import get_redis
from app.dependencies import SESSION_COOKIE_NAME, CurrentUser
from app.users.auth_service import create_session


@pytest.fixture
def client(fake_redis):
    """An app with one protected probe route, backed by the fake Redis."""
    built = FastAPI()
    install_error_handling(built)

    @built.get("/probe")
    async def probe(user: CurrentUser) -> dict[str, str]:
        return {"username": user.username}

    built.dependency_overrides[get_redis] = lambda: fake_redis
    return TestClient(built)


def test_a_request_without_a_session_cookie_is_refused(client):
    response = client.get("/probe")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


def test_a_request_with_an_unknown_token_is_refused(client):
    client.cookies.set(SESSION_COOKIE_NAME, "a-token-nobody-issued")

    response = client.get("/probe")

    assert response.status_code == 401


async def test_a_valid_session_reaches_the_route(client, fake_redis):
    session = await create_session(fake_redis, "researcher")
    client.cookies.set(SESSION_COOKIE_NAME, session.token)

    response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"username": "researcher"}


async def test_an_expired_session_is_refused(client, fake_redis, clock):
    # The dependency must not merely check that a cookie exists. This is the test that
    # fails if it ever stops consulting the session's deadline.
    session = await create_session(fake_redis, "researcher")
    client.cookies.set(SESSION_COOKIE_NAME, session.token)

    clock.advance(auth.settings.session_ttl_seconds + 1)

    assert client.get("/probe").status_code == 401


async def test_an_invalidated_session_is_refused_immediately(client, fake_redis):
    # Signing out must take effect on the next request, not when the TTL eventually lapses.
    session = await create_session(fake_redis, "researcher")
    client.cookies.set(SESSION_COOKIE_NAME, session.token)
    assert client.get("/probe").status_code == 200

    await auth.invalidate_session(fake_redis, session.token)

    assert client.get("/probe").status_code == 401


def test_the_refusal_carries_the_shared_error_envelope(client):
    # Constitution V: clients handle every endpoint identically, and a 401 is no exception.
    body = client.get("/probe").json()

    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["message"]


def test_the_refusal_does_not_describe_the_authentication_scheme(client):
    # A 401 should say what to do next (FR-052), not narrate how sessions are stored.
    message = client.get("/probe").json()["error"]["message"].lower()

    for leak in ("redis", "cookie", "token", "scrypt", "hash"):
        assert leak not in message
