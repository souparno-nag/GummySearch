"""FastAPI application setup.

Builds the application, installs the shared error handling, and owns the shutdown of
the connection pools created in `app/common/database.py` and `app/common/redis.py`.

`quickstart.md` starts the server with `uvicorn app.main:app`, so a module-level `app`
has to exist. It is built by `create_app()` rather than assembled inline so tests can
construct an isolated instance instead of mutating the shared one.

Routers are registered in `register_routers()`. It is empty for now because no module
has one yet — the first arrives with the audiences router in T058.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.common.database import dispose_engine
from app.common.middleware import install_error_handling
from app.common.redis import close_redis

API_TITLE = "JammySearch"
API_DESCRIPTION = "Reddit audience intelligence — group communities and read them as one audience."
API_VERSION = "0.1.0"


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

    Each of the six modules owns exactly one router, added here as it is built. Nothing
    is registered yet; `contracts/rest-api.md` remains the single source of truth for the
    HTTP surface, so no endpoint exists until the task that contracts it lands.
    """


def create_app() -> FastAPI:
    """Build and return a configured application instance. Touches no external system."""
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
