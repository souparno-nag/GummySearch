"""Unit tests for the shared Redis connection pool.

These never open a socket. Constitution IV forbids tests contacting external systems;
everything asserted here is pool configuration decided at import time, plus the
shutdown contract, which is exercised against recording stand-ins.
"""

from app.common.redis import (
    HEALTH_CHECK_INTERVAL_SECONDS,
    MAX_CONNECTIONS,
    close_redis,
    get_redis,
    pool,
    redis_client,
)
from app.config import settings


def test_the_pool_points_at_the_configured_redis():
    # Settings are read only in app/config.py; this module must not re-read the env.
    assert f"redis://{pool.connection_kwargs['host']}:{pool.connection_kwargs['port']}" in (
        settings.redis_url
    )


def test_pooled_connections_are_health_checked_before_reuse():
    # Without this, a connection the server dropped fails a request instead of being
    # transparently replaced.
    assert pool.connection_kwargs["health_check_interval"] == HEALTH_CHECK_INTERVAL_SECONDS


def test_the_pool_is_bounded():
    # A bounded pool turns a connection leak into a visible error rather than letting
    # it exhaust Redis's own connection limit.
    assert pool.max_connections == MAX_CONNECTIONS


def test_responses_are_decoded_to_text():
    # Everything stored is JSON text, so callers should never handle raw bytes.
    assert pool.connection_kwargs["decode_responses"] is True


def test_every_caller_shares_one_client():
    # A per-request client would build a second pool; closing one would break the rest.
    assert get_redis() is redis_client
    assert get_redis() is get_redis()


def test_the_client_is_bound_to_the_shared_pool():
    assert redis_client.connection_pool is pool


async def test_shutdown_closes_the_client_and_drains_the_pool(monkeypatch):
    calls = []

    async def fake_aclose():
        calls.append("client_closed")

    async def fake_disconnect():
        calls.append("pool_disconnected")

    monkeypatch.setattr("app.common.redis.redis_client.aclose", fake_aclose)
    monkeypatch.setattr("app.common.redis.pool.disconnect", fake_disconnect)

    await close_redis()

    assert calls == ["client_closed", "pool_disconnected"]
