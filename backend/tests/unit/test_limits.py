"""Unit tests for server-side rate limiting (FR-080).

FR-080's wording is the point of these tests: limits are "controls in their own right, never
interface conveniences", and `contracts/rest-api.md` sharpens it — "a client that omits the
check must not be able to exceed them". So the limiter is tested as something that refuses,
not as something that advises, and the dependency is driven through real HTTP requests that
carry no client-side cooperation whatsoever.

Time is passed explicitly rather than slept for, so window boundaries are exact
(Constitution IV). Redis is the fake from `tests/conftest.py`.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.common.limits as limits
from app.common.exceptions import RateLimitedError
from app.common.limits import (
    RATE_LIMIT_KEY_PREFIX,
    SIGNIN_BUCKET,
    consume_rate_limit,
    paid_call_rate_limit,
    rate_limit,
    signin_rate_limit,
)
from app.common.middleware import install_error_handling
from app.common.redis import get_redis
from app.dependencies import SESSION_COOKIE_NAME
from app.users.auth_service import create_session

WINDOW = 60


async def consume(redis, *, subject="researcher", bucket="ask", requests=3, now=0.0):
    return await consume_rate_limit(
        redis,
        bucket=bucket,
        subject=subject,
        requests=requests,
        window_seconds=WINDOW,
        now=now,
    )


# ---------------------------------------------------------------------------
# The counter
# ---------------------------------------------------------------------------


async def test_requests_within_the_limit_are_allowed(fake_redis):
    for _ in range(3):
        await consume(fake_redis)


async def test_the_request_past_the_limit_is_refused(fake_redis):
    for _ in range(3):
        await consume(fake_redis)

    with pytest.raises(RateLimitedError):
        await consume(fake_redis)


async def test_the_allowance_reports_what_is_left(fake_redis):
    first = await consume(fake_redis)
    second = await consume(fake_redis)

    assert first.remaining == 2
    assert second.remaining == 1


async def test_the_allowance_returns_in_the_next_window(fake_redis):
    for _ in range(3):
        await consume(fake_redis, now=0.0)

    # One second past the window boundary, the allowance is whole again.
    allowance = await consume(fake_redis, now=WINDOW + 1)

    assert allowance.remaining == 2


async def test_the_allowance_does_not_return_early(fake_redis):
    # The companion to the test above: an implementation that reset on every call would
    # pass that one and be no limiter at all.
    for _ in range(3):
        await consume(fake_redis, now=0.0)

    with pytest.raises(RateLimitedError):
        await consume(fake_redis, now=WINDOW - 1)


async def test_separate_callers_do_not_share_an_allowance(fake_redis):
    for _ in range(3):
        await consume(fake_redis, subject="researcher")

    # A second subject is unaffected — one caller cannot lock another out.
    await consume(fake_redis, subject="someone-else")


async def test_separate_buckets_do_not_share_an_allowance(fake_redis):
    # Exhausting the Ask allowance must not also block search: they are separate controls
    # over separate costs.
    for _ in range(3):
        await consume(fake_redis, bucket="ask")

    await consume(fake_redis, bucket="search")


async def test_the_refusal_says_how_long_to_wait(fake_redis):
    for _ in range(3):
        await consume(fake_redis, now=0.0)

    with pytest.raises(RateLimitedError) as raised:
        await consume(fake_redis, now=10.0)

    # FR-052: say what to do next. "Try later" is not actionable; a number is.
    assert raised.value.details["retry_after_seconds"] == WINDOW - 10


async def test_counters_expire_rather_than_accumulating_forever(fake_redis):
    await consume(fake_redis)

    key = [k async for k in fake_redis.scan_iter(match=f"{RATE_LIMIT_KEY_PREFIX}*")][0]

    assert await fake_redis.ttl(key) > 0


async def test_rate_limit_keys_do_not_collide_with_sessions(fake_redis):
    # Redis is shared. A counter must never be mistaken for a session, in either direction.
    await create_session(fake_redis, "researcher")
    await consume(fake_redis)

    counters = [k async for k in fake_redis.scan_iter(match=f"{RATE_LIMIT_KEY_PREFIX}*")]
    sessions = [k async for k in fake_redis.scan_iter(match="session:*")]

    assert len(counters) == 1
    assert len(sessions) == 1
    assert not set(counters) & set(sessions)


# ---------------------------------------------------------------------------
# The dependency, driven over HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def client(fake_redis):
    built = FastAPI()
    install_error_handling(built)

    @built.get("/costly", dependencies=[rate_limit("probe", requests=2, window_seconds=WINDOW)])
    async def costly() -> dict[str, bool]:
        return {"ok": True}

    built.dependency_overrides[get_redis] = lambda: fake_redis
    return TestClient(built)


async def sign_in(client, fake_redis):
    session = await create_session(fake_redis, "researcher")
    client.cookies.set(SESSION_COOKIE_NAME, session.token)


async def test_an_endpoint_serves_until_its_limit_is_reached(client, fake_redis):
    await sign_in(client, fake_redis)

    assert client.get("/costly").status_code == 200
    assert client.get("/costly").status_code == 200


async def test_an_endpoint_refuses_once_its_limit_is_reached(client, fake_redis):
    await sign_in(client, fake_redis)
    client.get("/costly")
    client.get("/costly")

    response = client.get("/costly")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


async def test_the_refusal_uses_the_shared_error_envelope(client, fake_redis):
    await sign_in(client, fake_redis)
    for _ in range(3):
        response = client.get("/costly")

    body = response.json()

    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}


async def test_the_default_allowance_comes_from_configuration(fake_redis, monkeypatch):
    # FR-080 treats limits as real controls. A control that needs a code change to adjust
    # is one an operator eventually disables instead, so the default is configurable.
    monkeypatch.setattr(limits.settings, "rate_limit_requests", 1)
    monkeypatch.setattr(limits.settings, "rate_limit_window_seconds", WINDOW)

    built = FastAPI()
    install_error_handling(built)

    @built.get("/default", dependencies=[paid_call_rate_limit("default")])
    async def endpoint() -> dict[str, bool]:
        return {"ok": True}

    built.dependency_overrides[get_redis] = lambda: fake_redis
    configured = TestClient(built)
    await sign_in(configured, fake_redis)

    assert configured.get("/default").status_code == 200
    assert configured.get("/default").status_code == 429


def test_an_unauthenticated_request_never_reaches_the_limited_endpoint(client):
    # The limiter is keyed on the caller, so authentication has to resolve first. This also
    # means an anonymous flood cannot consume the signed-in user's allowance.
    assert client.get("/costly").status_code == 401


# ---------------------------------------------------------------------------
# The client-keyed variant, for endpoints with no signed-in caller
# ---------------------------------------------------------------------------


@pytest.fixture
def open_client(fake_redis, monkeypatch):
    """An app with one unauthenticated endpoint behind the sign-in limiter."""
    monkeypatch.setattr(limits.settings, "signin_rate_limit_requests", 2)
    monkeypatch.setattr(limits.settings, "signin_rate_limit_window_seconds", WINDOW)

    built = FastAPI()
    install_error_handling(built)

    @built.post("/open", dependencies=[signin_rate_limit()])
    async def open_endpoint() -> dict[str, bool]:
        return {"ok": True}

    built.dependency_overrides[get_redis] = lambda: fake_redis
    return TestClient(built)


def test_an_unauthenticated_endpoint_serves_until_its_limit_is_reached(open_client):
    # No session, no cookie: this is the case `rate_limit` structurally cannot cover, since
    # it resolves CurrentUser before counting.
    assert open_client.post("/open").status_code == 200
    assert open_client.post("/open").status_code == 200


def test_an_unauthenticated_endpoint_refuses_once_its_limit_is_reached(open_client):
    open_client.post("/open")
    open_client.post("/open")

    response = open_client.post("/open")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"


async def test_the_client_keyed_limit_counts_against_the_calling_address(open_client, fake_redis):
    open_client.post("/open")

    keys = [
        k async for k in fake_redis.scan_iter(match=f"{RATE_LIMIT_KEY_PREFIX}{SIGNIN_BUCKET}:*")
    ]

    # Keyed on the caller's address, not on a username that does not exist yet.
    assert len(keys) == 1
    assert "testclient" in keys[0]


def test_the_client_keyed_limit_ignores_a_forwarded_for_header(open_client):
    # The header is client-controlled. Honouring it would let a caller mint a fresh subject
    # per attempt and walk straight through the limit.
    for index in range(2):
        assert (
            open_client.post("/open", headers={"X-Forwarded-For": f"10.0.0.{index}"}).status_code
            == 200
        )

    refused = open_client.post("/open", headers={"X-Forwarded-For": "10.0.0.99"})

    assert refused.status_code == 429


def test_the_sign_in_allowance_comes_from_configuration(open_client, monkeypatch):
    # Read per request rather than captured when the route was declared, so raising the
    # allowance does not need the app rebuilt.
    open_client.post("/open")
    open_client.post("/open")
    assert open_client.post("/open").status_code == 429

    monkeypatch.setattr(limits.settings, "signin_rate_limit_requests", 10)

    assert open_client.post("/open").status_code == 200


async def test_sign_in_counters_do_not_share_a_bucket_with_paid_calls(open_client, fake_redis):
    # Exhausting sign-in attempts must not also refuse a signed-in caller's Ask allowance.
    open_client.post("/open")
    await consume(fake_redis, bucket="ask")

    signin = [
        k async for k in fake_redis.scan_iter(match=f"{RATE_LIMIT_KEY_PREFIX}{SIGNIN_BUCKET}:*")
    ]
    ask = [k async for k in fake_redis.scan_iter(match=f"{RATE_LIMIT_KEY_PREFIX}ask:*")]

    assert not set(signin) & set(ask)
