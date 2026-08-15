"""Typed application exceptions.

Every failure a module wants the user to see is raised as one of these. The shared
handlers in `app/common/middleware.py` are the only thing that turns them into HTTP
responses, so a router never assembles an error body and no raw exception text ever
reaches a client (Constitution V, FR-052).

Each exception carries three things the envelope needs:

- `status_code` — the HTTP status, fixed per class rather than chosen at the raise site,
  so the same kind of failure always answers with the same status.
- `code` — a stable machine-readable string the frontend can branch on. Class defaults
  are deliberately generic; a module raising for a specific reason passes its own, e.g.
  `raise ConflictError("...", code="audience_limit_reached")`.
- `message` — human-readable, stating what failed and what to do next (FR-052).

`details` carries structured, non-sensitive context (which field was invalid, which
community was unavailable). It is optional and must never hold internal state,
credentials, or a traceback.
"""

from typing import Any


class AppError(Exception):
    """Base class for every failure this application reports to a user.

    Raising `AppError` directly is possible but rarely right — a subclass carries the
    correct HTTP status. Anything that escapes uncaught and is *not* an `AppError` is
    treated as an unexpected bug and reported as a generic 500, with the detail kept
    server-side.
    """

    status_code: int = 500
    code: str = "internal_error"
    message: str = "Something went wrong. Please try again."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or type(self).message
        self.code = code or type(self).code
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)


class ValidationError(AppError):
    """The request was well-formed but its contents are not acceptable."""

    status_code = 422
    code = "invalid_request"
    message = "The request could not be processed. Check the values you supplied and try again."


class NotFoundError(AppError):
    """The requested resource does not exist, or is not this user's to see."""

    status_code = 404
    code = "not_found"
    message = "That item no longer exists. It may have been deleted."


class ConflictError(AppError):
    """The request is valid but conflicts with the current state.

    Used for limits and duplicates — an audience already holding 50 communities, a
    community already in the audience — where retrying unchanged will not help.
    """

    status_code = 409
    code = "conflict"
    message = "That change conflicts with the current state. Refresh and try again."


class AuthenticationError(AppError):
    """No valid session. Distinct from `AuthorizationError`: nobody is signed in."""

    status_code = 401
    code = "not_authenticated"
    message = "Your session has expired. Sign in again to continue."


class AuthorizationError(AppError):
    """A valid session, but not permitted to act on this resource."""

    status_code = 403
    code = "not_permitted"
    message = "You do not have access to that item."


class RateLimitedError(AppError):
    """The caller exceeded a server-side request rate limit (FR-080)."""

    status_code = 429
    code = "rate_limited"
    message = "Too many requests. Wait a moment and try again."


class SpendCeilingError(AppError):
    """A configured spend ceiling is exhausted, so a paid call was refused (FR-046).

    Deliberately distinct from `RateLimitedError`: waiting does not help, because the
    ceiling resets on its own schedule or must be raised in configuration.
    """

    status_code = 429
    code = "spend_ceiling_reached"
    message = (
        "The configured spend limit for this period has been reached. "
        "Raise the limit or wait for the period to reset."
    )


class UpstreamError(AppError):
    """An external system this project depends on failed or timed out.

    Covers Reddit and the model provider. Kept separate from `AppError` so a provider
    outage is never reported as an application bug — and, for the Ask feature, so a
    `failed` outcome is never confused with a `refused` one (SC-007).
    """

    status_code = 502
    code = "upstream_unavailable"
    message = "An external service is not responding right now. Try again shortly."


class DegradedError(AppError):
    """The request needs a capability that is currently unavailable (FR-049).

    Raised only where the feature genuinely cannot proceed. Where collected material can
    still be served with a staleness notice, that is the correct behaviour instead of
    this exception.
    """

    status_code = 503
    code = "temporarily_degraded"
    message = (
        "This feature is temporarily unavailable. Previously collected material is unaffected."
    )
