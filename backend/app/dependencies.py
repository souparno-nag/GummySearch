"""Shared FastAPI dependencies.

Dependencies used by more than one module live here rather than in whichever module
happened to need one first (Constitution II). Module-specific dependencies stay with their
module.

`CurrentUser` is the gate every authenticated route sits behind. FR-048 requires sign-in
and requires the user's audiences, bookmarks, and notes to stay private to them; this is
where "is anybody signed in" is decided, once, so no router repeats the check and none of
them can quietly omit it.

External systems touched: Redis, via the session lookup.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.common.exceptions import AuthenticationError
from app.common.redis import get_redis
from app.users.auth_service import Session, get_session

# The cookie carrying the session token. A cookie rather than an `Authorization` header
# because the client is a browser SPA (R10): a cookie can be `HttpOnly`, which puts the
# token out of reach of any script on the page, and FR-079 requires secrets to be
# unreadable by the client.
SESSION_COOKIE_NAME = "jammysearch_session"


@dataclass(frozen=True)
class AuthenticatedUser:
    """The signed-in principal, as routes and services see it.

    A deliberate seam. This deployment is single-user with the credential in configuration
    (R11), so there is no `User` row to load and the session carries everything anyone
    needs. When bookmarks arrive (T137) and a persisted user exists, this is the one place
    that changes — routes depending on `CurrentUser` keep working unchanged.
    """

    username: str
    session: Session


async def get_current_user(
    request: Request,
    redis: Annotated[Redis, Depends(get_redis)],
) -> AuthenticatedUser:
    """Resolve the signed-in user from the request's session cookie.

    Raises `AuthenticationError` when no cookie is present, when the token is unknown, or
    when the session has expired or been invalidated — all reported as the same 401 so a
    caller cannot learn which of those applied.

    External systems touched: Redis.
    """
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise AuthenticationError("Sign in to continue.")
    session = await get_session(redis, token)
    if session is None:
        # Covers unknown, malformed, expired, and invalidated alike. Distinguishing them
        # for the caller would tell an unauthenticated party which tokens once existed.
        raise AuthenticationError()
    return AuthenticatedUser(username=session.username, session=session)


# Declared as an annotated alias so routes write `user: CurrentUser` rather than repeating
# `= Depends(...)` in an argument default — ruff's B008 rejects the latter, and this is
# FastAPI's own recommended form.
CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
