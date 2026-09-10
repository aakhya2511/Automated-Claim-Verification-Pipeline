"""ASGI application factory.

Kept deliberately thin: it configures logging, builds the dependency graph via
:func:`app.bootstrap.build_container`, mounts routers, and registers exception
handlers. No business logic lives here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.api.routes import claims, health
from app.bootstrap import ServiceContainer, build_container
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

DESCRIPTION = """
Verifies natural-language commercial claims against an authoritative reference
dataset using a hybrid pipeline: deterministic normalization and rules first,
an LLM semantic rater only for claims the rules cannot settle.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build dependencies at startup; fail fast if the environment is wrong.

    Loading and indexing the reference catalog here (rather than lazily on
    first request) means a bad deploy is caught by the readiness probe instead
    of by a user, and keeps catalog I/O off the per-claim latency path.
    """
    settings: Settings = app.state.settings
    container: ServiceContainer = build_container(settings=settings)
    app.state.container = container
    logger.info("service_started", version=__version__, environment=settings.environment)
    try:
        yield
    finally:
        await container.aclose()
        logger.info("service_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(
        level=resolved.observability.log_level,
        log_format=resolved.observability.log_format,
    )

    app = FastAPI(
        title="Automated Claim Verification Pipeline",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = resolved

    # Starlette applies the last-added middleware outermost. Request identity
    # wraps the cheap size check so even early 4xx responses remain traceable.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=resolved.server.max_request_bytes)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(claims.router)
    return app


app = create_app()
