"""Translation of exceptions into the shared error envelope.

Every error response the API produces is built here and nowhere else. A router that
assembles an error body is a bug (Constitution III fixes the envelope shape; FR-052
fixes what the message must say).

The envelope, per `contracts/rest-api.md`:

    {"error": {"code": "audience_limit_reached", "message": "...", "details": {}}}

Four sources of failure are covered, because anything not covered would fall through
to Starlette's default plain-text response and break the shape clients rely on:

1. `AppError` — this project's typed exceptions (T013). Reported with their own status.
2. `RequestValidationError` — FastAPI rejecting a malformed request before the route
   runs. Its raw form leaks internal model structure, so it is reshaped.
3. `HTTPException` — raised by Starlette itself and by FastAPI's own dependencies
   (a 405, a 404 for an unrouted path).
4. Any other exception — an unexpected bug. Reported as a generic 500 with the detail
   kept server-side.

These are registered as **exception handlers** rather than as `BaseHTTPMiddleware`.
Middleware wraps the call stack from outside, which means it cannot see an exception
FastAPI has already handled internally — `RequestValidationError` in particular never
reaches it. Handlers are the mechanism that sees all four cases.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.exceptions import AppError

logger = logging.getLogger(__name__)

# Codes for the statuses Starlette raises on its own behalf, so a 404 from an unrouted
# path is indistinguishable in shape from a 404 raised by application code.
_STATUS_CODES: dict[int, str] = {
    401: "not_authenticated",
    403: "not_permitted",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "invalid_request",
    429: "rate_limited",
    503: "temporarily_degraded",
}

GENERIC_ERROR_MESSAGE = "Something went wrong on our end. Try again in a moment."


def build_envelope(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return the error envelope body. Touches no external system."""
    return {"error": {"code": code, "message": message, "details": details or {}}}


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    """Report a typed application error with its own status, code, and message."""
    # Expected failures are not bugs, so this logs at warning rather than raising alarm,
    # and carries no traceback.
    logger.warning(
        "application error",
        extra={"code": exc.code, "status": exc.status_code, "path": request.url.path},
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=build_envelope(exc.code, exc.message, exc.details),
    )


async def handle_validation_error(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """Reshape FastAPI's request-validation failure into the envelope.

    FastAPI's default body exposes the internal location tuple of every invalid field.
    Only the field name and the reason are echoed back, so the response says what to fix
    without describing the models behind it.
    """
    fields = [
        {
            # Drop the leading "body"/"query" segment — the caller knows where they put it.
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "reason": error["msg"],
        }
        for error in exc.errors()
    ]
    logger.warning(
        "request validation failed",
        extra={"path": request.url.path, "field_count": len(fields)},
    )
    return JSONResponse(
        status_code=422,
        content=build_envelope(
            "invalid_request",
            "Some values in the request were not accepted. Correct them and try again.",
            {"fields": fields},
        ),
    )


async def handle_http_exception(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Wrap an exception raised by the framework itself in the envelope."""
    code = _STATUS_CODES.get(exc.status_code, "request_failed")
    # `exc.detail` here is Starlette's own wording ("Not Found"), never application
    # internals, so it is safe to surface.
    message = str(exc.detail) if exc.detail else GENERIC_ERROR_MESSAGE
    return JSONResponse(status_code=exc.status_code, content=build_envelope(code, message))


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Report an unhandled exception as a generic 500, keeping the detail server-side.

    The traceback is logged, never returned: Constitution V forbids raw exception text
    reaching a client. If this fires, the correct fix is usually a new typed exception in
    `app/common/exceptions.py`, not a special case here.
    """
    logger.exception("unhandled exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content=build_envelope("internal_error", GENERIC_ERROR_MESSAGE),
    )


def install_error_handling(app: FastAPI) -> None:
    """Register every error handler on the application. Touches no external system."""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(Exception, handle_unexpected_error)
