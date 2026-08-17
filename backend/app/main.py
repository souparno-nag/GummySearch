"""FastAPI application setup.

Builds the application, installs the shared error handling, and owns the shutdown of
the connection pools created in `app/common/database.py` and `app/common/redis.py`.

`quickstart.md` starts the server with `uvicorn app.main:app`, so a module-level `app`
has to exist. It is built by `create_app()` rather than assembled inline so tests can
construct an isolated instance instead of mutating the shared one.

Routers are registered in `register_routers()`. It currently carries only the users
router's three session endpoints (T180); the audiences router arrives with T058.

This module also owns the startup bind guard (FR-078, R17). It runs during `create_app()`,
so an unsafe bind stops the process before uvicorn ever opens a socket.
"""

import ipaddress
import logging
import sys
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.common.database import dispose_engine
from app.common.middleware import install_error_handling
from app.common.redis import close_redis
from app.config import settings
from app.users.router import router as users_router

logger = logging.getLogger(__name__)

API_TITLE = "JammySearch"
API_DESCRIPTION = "Reddit audience intelligence — group communities and read them as one audience."
API_VERSION = "0.1.0"

# The setting that permits a non-local bind. Named here as a constant because the refusal
# message quotes it — an operator who is refused needs to be told what to change.
EXPOSURE_SETTING = "ALLOW_REMOTE_EXPOSURE"


class UnsafeBindError(RuntimeError):
    """The application was asked to bind a non-local interface without permission.

    Deliberately *not* an `AppError`. Everything in `app/common/exceptions.py` exists to
    become an HTTP response, and this failure has no request to answer — it happens before
    the server is listening. Raising it aborts startup, which is precisely the behaviour
    the specification's final edge case requires.
    """


def is_loopback(host: str) -> bool:
    """Return whether `host` refers to the local machine only.

    Accepts the forms uvicorn does: a bare address, an IPv6 address in brackets, or the
    name `localhost`. Anything unrecognised — including a hostname that would need DNS to
    resolve — is reported as *not* loopback, so the guard fails closed. Resolution is
    deliberately not attempted: it needs a network, and its answer is not a stable fact.

    Touches no external system; in particular it performs no name resolution.
    """
    candidate = host.strip().strip("[]").lower()
    if not candidate:
        return False
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def resolve_bind_host(argv: Sequence[str] | None = None) -> str:
    """Return the interface the application is about to bind.

    The command line wins over configuration, because that is how exposure actually
    happens: `quickstart.md` starts the server with the uvicorn CLI, so someone opening
    this application to their network types `--host 0.0.0.0` rather than editing a
    setting. A guard consulting only configuration would inspect the loopback default,
    approve it, and let uvicorn bind every interface regardless.

    Falls back to `settings.app_host` when there is no `--host` argument, which covers
    programmatic startup. Touches no external system.
    """
    arguments = list(sys.argv if argv is None else argv)
    for index, argument in enumerate(arguments):
        if argument == "--host" and index + 1 < len(arguments):
            return arguments[index + 1]
        if argument.startswith("--host="):
            return argument.split("=", 1)[1]
    return settings.app_host


def assert_bind_allowed(host: str, *, allow_remote_exposure: bool) -> None:
    """Permit a local bind, or a remote one that was explicitly opted into.

    Raises `UnsafeBindError` otherwise. The flag is a parameter rather than something read
    from settings here so the rule stays a pure function of its two inputs — which is what
    makes it exhaustively testable without a network or a patched environment.

    Touches no external system.
    """
    if is_loopback(host):
        return
    if allow_remote_exposure:
        # Not an error, but never silent: this is the one line in the log that explains
        # why the tool is reachable from off the machine.
        logger.warning(
            "binding a non-local interface because %s is set — deployment-layer "
            "protections (transport security, abuse protection, monitoring) are "
            "deliberately not part of this release (FR-081)",
            EXPOSURE_SETTING,
            extra={"host": host},
        )
        return
    raise UnsafeBindError(
        f"Refusing to start: {host!r} is not a local address, and {EXPOSURE_SETTING} is not "
        f"set. This application is intended to run on your machine only. To bind it "
        f"anyway, set {EXPOSURE_SETTING}=true in .env — and read FR-081 first, because "
        f"transport security, abuse protection, and monitoring are not part of this "
        f"release. To keep it local, start it on 127.0.0.1."
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup and shutdown work around the application's serving lifetime.

    There is deliberately no startup work. The database engine and the Redis client both
    connect lazily on first use, so eagerly connecting here would only mean the server
    refuses to start when a dependency is briefly down — worse than serving the requests
    that do not need it.

    Shutdown closes both pools so the process does not exit holding connections open on
    PostgreSQL and Redis.

    External systems touched: PostgreSQL and Redis, on shutdown only.
    """
    yield
    await dispose_engine()
    await close_redis()


def register_routers(app: FastAPI) -> None:
    """Attach every module's router to the application.

    Each of the six modules owns exactly one router, added here as it is built.
    `contracts/rest-api.md` remains the single source of truth for the HTTP surface, so no
    endpoint may appear here ahead of the task that contracts it.

    The users router carries the three session endpoints — the only routes exempt from
    requiring a session, because they are what issues one.
    """
    app.include_router(users_router)


def create_app() -> FastAPI:
    """Build and return a configured application instance.

    Runs the bind guard first, so an unsafe bind aborts before anything is constructed and
    long before uvicorn opens a socket. Touches no external system.
    """
    assert_bind_allowed(
        resolve_bind_host(),
        allow_remote_exposure=settings.allow_remote_exposure,
    )
    app = FastAPI(
        title=API_TITLE,
        description=API_DESCRIPTION,
        version=API_VERSION,
        lifespan=lifespan,
    )
    install_error_handling(app)
    register_routers(app)
    return app


app = create_app()
