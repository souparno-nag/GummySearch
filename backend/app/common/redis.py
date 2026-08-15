"""Shared async Redis connection pool.

Redis serves two distinct roles in this project: it is the cache in front of the
Reddit API (Constitution I, with explicit TTLs set in `app/reddit/cache.py`), and it
is Celery's broker. This module owns only the first — the application-side client.
Celery configures its own connection from `settings.redis_url` in
`workers/celery_app.py` and does not share this pool, because a worker process and a
web process have different lifetimes and different concurrency.

Unlike the database session in `app/common/database.py`, there is no per-request
Redis object. Redis commands are individually atomic and there is no transaction to
open or close, so every caller shares one long-lived client over one pool.

External systems touched: Redis.
"""

from redis.asyncio import ConnectionPool, Redis

from app.config import settings

# Sized for a single-user tool: comfortably above the concurrency FastAPI plus the
# background workers will produce, while still bounded so a connection leak surfaces
# as an error rather than exhausting Redis's own limit.
MAX_CONNECTIONS = 20

# Redis's answer to the database pool's pre-ping. A pooled connection idle for longer
# than this is pinged before reuse, so a connection the server dropped — a container
# restart, an idle timeout — is replaced transparently instead of failing a request.
HEALTH_CHECK_INTERVAL_SECONDS = 30

# `decode_responses=True` makes the client return `str` rather than `bytes`. Everything
# this project stores in Redis is text (JSON-serialized Reddit listings), so decoding
# once at the client is simpler than every call site remembering to `.decode()`.
pool: ConnectionPool = ConnectionPool.from_url(
    settings.redis_url,
    max_connections=MAX_CONNECTIONS,
    health_check_interval=HEALTH_CHECK_INTERVAL_SECONDS,
    decode_responses=True,
)

# Constructing the client opens no socket; the first connection is made lazily on the
# first command, so importing this module performs no I/O.
redis_client: Redis = Redis(connection_pool=pool)


def get_redis() -> Redis:
    """Return the process-wide Redis client.

    Intended as the FastAPI dependency for routes and services needing the cache. It
    returns the *shared* client rather than yielding a per-request one: the client is
    stateless and closing it would tear down the pool for every other caller.

    External systems touched: Redis (lazily, on first command).
    """
    return redis_client


async def close_redis() -> None:
    """Close the client and disconnect every pooled connection.

    Called on application shutdown so the process does not exit leaving connections
    open on the Redis server.

    External systems touched: Redis.
    """
    await redis_client.aclose()
    await pool.disconnect()
