"""Server-side request rate limiting.

FR-080 is unusually specific about what this must be:

> Spend ceilings (FR-046) and request rate limits MUST be enforced on the server as
> controls in their own right, never as interface conveniences, so that they remain
> effective if the deployment is later exposed.

And `contracts/rest-api.md` adds the test of whether that has been achieved: "a client that
omits the check must not be able to exceed them." So nothing here advises a client. The
limiter refuses, from a FastAPI dependency that runs before the route body, and a caller
that never reads a header is bound by it exactly as much as one that does.

Counters live only in Redis. The constitution's Technology and Data Constraints section
names rate-limit windows as ephemeral operational state, exempt from the rule that no
durable data may exist only in Redis — a lost counter is refilled by the next request in its
window, and persisting one would put a write on the system of record in front of every
request this guards.

This module covers **request rate**. The **spend ceiling** half of FR-080 is a different
control with a different unit — money rather than requests — and lands with `app/ops/`
(T031). Both exist because they fail differently: a rate limit protects the service from
volume, a spend ceiling protects the wallet from a single expensive call.

External systems touched: Redis.
"""

import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, params
from redis.asyncio import Redis

from app.common.exceptions import RateLimitedError
from app.common.redis import get_redis
from app.config import settings
from app.dependencies import CurrentUser

# Namespaced so counters are never confused with sessions, cached Reddit listings, or
# anything else sharing this Redis.
RATE_LIMIT_KEY_PREFIX = "ratelimit:"

# Sign-in has its own bucket so exhausting it cannot lock a signed-in caller out of anything
# else, and so a flood of sign-in attempts is visible as itself.
SIGNIN_BUCKET = "signin"


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


def signin_rate_limit() -> params.Depends:
    """Return a dependency bounding sign-in attempts, keyed on the calling client.

    `rate_limit` above keys on `CurrentUser`, which makes it structurally unusable on the one
    endpoint that needs it most: at sign-in, establishing that user is the point. This keys on
    the calling client's address instead, and shares `consume_rate_limit` unchanged so there
    is one counting implementation rather than two.

    Attempts are counted whatever their outcome, because the dependency runs before the route
    body. A limiter that counted only failures would be evaded by a caller who does not read
    the response, which is exactly the client FR-080 says the control must still bind.

    The subject is `request.client.host`, deliberately **not** `X-Forwarded-For`. That header
    is set by the client, so trusting it would let a caller mint a fresh subject per attempt
    and walk straight through this limit. Behind a reverse proxy that means every caller keys
    to the proxy — a deployment concern FR-081 defers, not a reason to trust a spoofable
    header now.

    The allowance is read from configuration **per request** rather than captured when the
    route is declared, so it can be changed without a code change (FR-080's reasoning: a
    control that needs a rebuild to adjust is one an operator eventually works around).

    FR-081 excludes *network-level* brute-force protection from this release. This is an
    application-level control costing one Redis `INCR`, and FR-079 forbids leaning on the
    loopback bind, so it stays.

    External systems touched: Redis, when the returned dependency runs.
    """

    async def enforce(
        request: Request,
        redis: Annotated[Redis, Depends(get_redis)],
    ) -> Allowance:
        # A missing `client` happens on ASGI transports that report no peer. Falling back to
        # a constant shares one allowance between such callers, which errs toward refusing
        # rather than toward an unbounded endpoint.
        subject = request.client.host if request.client else "unknown"
        return await consume_rate_limit(
            redis,
            bucket=SIGNIN_BUCKET,
            subject=subject,
            requests=settings.signin_rate_limit_requests,
            window_seconds=settings.signin_rate_limit_window_seconds,
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
