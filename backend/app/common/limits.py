"""Server-side request rate limiting.

FR-080 is unusually specific about what this must be:

> Spend ceilings (FR-046) and request rate limits MUST be enforced on the server as
> controls in their own right, never as interface conveniences, so that they remain
> effective if the deployment is later exposed.

And `contracts/rest-api.md` adds the test of whether that has been achieved: "a client that
omits the check must not be able to exceed them." So nothing here advises a client. The
limiter refuses, from a FastAPI dependency that runs before the route body, and a caller
that never reads a header is bound by it exactly as much as one that does.

This module covers **request rate**. The **spend ceiling** half of FR-080 is a different
control with a different unit — money rather than requests — and lands with `app/ops/`
(T031). Both exist because they fail differently: a rate limit protects the service from
volume, a spend ceiling protects the wallet from a single expensive call.

External systems touched: Redis.
"""

import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, params
from redis.asyncio import Redis

from app.common.exceptions import RateLimitedError
from app.common.redis import get_redis
from app.config import settings
from app.dependencies import CurrentUser

# Namespaced so counters are never confused with sessions, cached Reddit listings, or
# anything else sharing this Redis.
RATE_LIMIT_KEY_PREFIX = "ratelimit:"


@dataclass(frozen=True)
class Allowance:
    """What is left of a caller's allowance in the current window."""

    remaining: int
    reset_in_seconds: int


async def consume_rate_limit(
    redis: Redis,
    *,
    bucket: str,
    subject: str,
    requests: int,
    window_seconds: int,
    now: float | None = None,
) -> Allowance:
    """Count one request against `subject`'s allowance for `bucket`, or refuse it.

    Raises `RateLimitedError` when the allowance is already spent, carrying the seconds
    until it returns so the caller is told what to do next rather than merely "no"
    (FR-052).

    Counting is a fixed window: the window's start index is part of the Redis key, so a new
    window is a new key and expiry needs no separate bookkeeping. The known trade-off is
    that a caller can spend a full allowance at the end of one window and another at the
    start of the next, briefly doubling the nominal rate. That is acceptable here — this
    limit exists to stop runaway loops and to bound cost, not to smooth traffic — and a
    sliding-window log would cost memory proportional to request volume to fix a burst that
    does no harm at this scale.

    `now` is a parameter so window boundaries can be asserted exactly in tests
    (Constitution IV requires wall-clock time to be frozen).

    External systems touched: Redis.
    """
    moment = time.time() if now is None else now
    window_index = int(moment // window_seconds)
    key = f"{RATE_LIMIT_KEY_PREFIX}{bucket}:{subject}:{window_index}"

    used = await redis.incr(key)
    if used == 1:
        # Only on the first request of a window: the key is window-specific, so its TTL
        # never needs extending, and re-setting it on every request would let a steady
        # stream of requests keep a counter alive past its own window.
        await redis.expire(key, window_seconds)

    window_ends_at = (window_index + 1) * window_seconds
    reset_in = max(0, int(window_ends_at - moment))

    if used > requests:
        raise RateLimitedError(
            f"You have made too many requests. Try again in {reset_in} seconds.",
            details={"retry_after_seconds": reset_in, "limit": requests},
        )
    return Allowance(remaining=requests - used, reset_in_seconds=reset_in)


def rate_limit(bucket: str, *, requests: int, window_seconds: int) -> params.Depends:
    """Return a dependency enforcing `requests` per `window_seconds` on one endpoint.

    Attach it to a route that can trigger a paid call:

        @router.post("/audiences/{id}/ask", dependencies=[rate_limit("ask", ...)])

    The allowance is per signed-in caller and per bucket, so exhausting Ask does not also
    block search, and one caller cannot lock out another. Because the limit is keyed on the
    caller, authentication resolves first — which also means an unauthenticated flood is
    refused at the door rather than consuming the real user's allowance.

    External systems touched: Redis, when the returned dependency runs.
    """

    async def enforce(
        user: CurrentUser,
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> Allowance:
        return await consume_rate_limit(
            redis,
            bucket=bucket,
            subject=user.username,
            requests=requests,
            window_seconds=window_seconds,
        )

    return Depends(enforce)


def paid_call_rate_limit(bucket: str) -> params.Depends:
    """Return a dependency enforcing the configured default allowance for paid calls.

    The default lives in configuration rather than in this source file because FR-080
    treats these as real controls, and a control that requires a code change to adjust is
    one an operator eventually works around instead. Endpoints with their own cost profile
    can still call `rate_limit` directly with explicit numbers.

    External systems touched: Redis, when the returned dependency runs.
    """
    return rate_limit(
        bucket,
        requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
