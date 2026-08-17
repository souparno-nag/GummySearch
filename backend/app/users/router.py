"""The session endpoints: sign in, sign out, read the current session.

Contracted in `contracts/rest-api.md` under "Sessions". These are the only three endpoints
exempt from carrying a session, and the contract states that the list is exhaustive — every
other route requires `CurrentUser`.

They exist because T017–T022 built everything needed to *carry* a session and nothing that
*issues* one: until this router, `create_session` could only be called in-process, so no HTTP
client could ever satisfy `CurrentUser`.

Nothing here assembles an error response. Typed exceptions are raised and
`app/common/middleware.py` renders them, per Constitution III — a router that formats its own
error envelope is how two endpoints end up disagreeing about the shape of a failure.

External systems touched: Redis, via the session store and the rate limiter.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response, status
from redis.asyncio import Redis

from app.common.exceptions import AuthenticationError
from app.common.limits import signin_rate_limit
from app.common.redis import get_redis
from app.dependencies import SESSION_COOKIE_NAME, CurrentUser
from app.users.auth_service import authenticate, create_session, invalidate_session
from app.users.schemas import SessionResponse, SignInRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["sessions"])

SESSION_PATH = "/session"

# `Secure` is absent deliberately, and conditionally: this release serves plain HTTP on
# loopback and FR-081 defers transport security to the deployment layer. The contract records
# that the flag becomes mandatory alongside ALLOW_REMOTE_EXPOSURE, since without it the cookie
# would travel in clear text. `SameSite=Lax` is set now because it costs nothing.
COOKIE_SAMESITE = "lax"
COOKIE_PATH = "/"


def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """Attach the session cookie, `HttpOnly` so no script on the page can read the token."""
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=max_age,
        httponly=True,
        samesite=COOKIE_SAMESITE,
        path=COOKIE_PATH,
    )


@router.post(
    SESSION_PATH,
    response_model=SessionResponse,
    dependencies=[signin_rate_limit()],
)
async def sign_in(
    credentials: SignInRequest,
    response: Response,
    redis: Annotated[Redis, Depends(get_redis)],
) -> SessionResponse:
    """Sign in, setting the session cookie.

    Every failure — wrong password, unknown username, and a deployment with no
    `AUTH_PASSWORD_HASH` configured — raises the same `AuthenticationError`, so the responses
    are indistinguishable. Telling them apart would let a caller enumerate usernames, or learn
    that this deployment has no credential set at all.

    The attempt is counted by `signin_rate_limit` before this runs, whatever its outcome.

    External systems touched: Redis.
    """
    if not authenticate(credentials.username, credentials.password):
        # Logged without the attempted password, and without saying which check failed.
        logger.info("sign-in refused")
        raise AuthenticationError("Those sign-in details are not correct.")

    session = await create_session(redis, credentials.username)
    # Derived from the session itself rather than read from configuration a second time, so
    # the cookie's lifetime cannot drift from the session's own deadline.
    lifetime = int((session.expires_at - session.created_at).total_seconds())
    _set_session_cookie(response, session.token, max_age=lifetime)
    # The token is in the cookie only — deliberately not in this body (FR-079).
    return SessionResponse(username=session.username, expires_at=session.expires_at)


@router.get(SESSION_PATH, response_model=SessionResponse)
async def read_session(user: CurrentUser) -> SessionResponse:
    """Return the current session, or refuse with the standard 401.

    This is what lets a client-side-only SPA (R10) decide on a cold load whether to render the
    sign-in screen, rather than firing a request at an unrelated data route and interpreting
    the 401 that comes back.

    External systems touched: Redis, via `CurrentUser`.
    """
    return SessionResponse(username=user.username, expires_at=user.session.expires_at)


@router.delete(SESSION_PATH, status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(
    response: Response,
    redis: Annotated[Redis, Depends(get_redis)],
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> None:
    """End the current session immediately.

    Deliberately **not** behind `CurrentUser`: signing out with an already-expired session must
    succeed, and a caller trying to discard a token they think is compromised should never be
    refused because it turned out to be invalid already.

    Returns `204` whether or not the token named a live session. Reporting the difference would
    confirm which tokens are real, turning sign-out into an oracle. The cookie is cleared either
    way, so a dead token is not resent on every later request.

    External systems touched: Redis.
    """
    if session_token:
        await invalidate_session(redis, session_token)
    response.delete_cookie(SESSION_COOKIE_NAME, path=COOKIE_PATH)
