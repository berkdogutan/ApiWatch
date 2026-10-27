"""FastAPI application factory and development server entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.config import Settings, get_settings
from app.database import init_db, make_engine, make_session_factory
from app.routers import monitors, pages, stats
from app.services.checker import build_http_client
from app.services.monitoring import MonitorRunner
from app.services.scheduler import MonitorScheduler

STATIC_DIR = Path(__file__).resolve().parent / "static"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


async def browser_protection(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Reject cross-origin writes (CSRF) and forbid framing (clickjacking).

    Browsers send ``Origin`` with POST/PUT/DELETE; it must match the ``Host`` the request
    was sent to. Clients that send no ``Origin`` (curl, scripts) are not affected.
    """
    origin = request.headers.get("origin")
    if (
        request.method in UNSAFE_METHODS
        and origin is not None
        and urlsplit(origin).netloc != request.headers.get("host")
    ):
        return JSONResponse({"detail": "Cross-origin request rejected"}, status_code=403)
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


def create_app(
    settings: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the application.

    ``transport`` lets tests swap in an ``httpx.MockTransport`` so no real network is used.
    """
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    init_db(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with build_http_client(transport) as client:
            app.state.runner = MonitorRunner(session_factory, client, settings)
            app.state.scheduler = None
            if settings.scheduler_enabled:
                app.state.scheduler = MonitorScheduler(app.state.runner)
                app.state.scheduler.start()
            try:
                yield
            finally:
                if app.state.scheduler is not None:
                    app.state.scheduler.shutdown()
        engine.dispose()

    app = FastAPI(
        title="APIWatch",
        version=__version__,
        description="Lightweight API monitoring: uptime, response times, incidents and "
        "JSON change detection.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.scheduler = None
    app.middleware("http")(browser_protection)
    # Added last so it runs first: unknown Host headers never reach the app.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(monitors.router)
    app.include_router(stats.router)
    app.include_router(pages.router)
    return app


def run() -> None:
    """``uv run apiwatch`` - start the development server (single worker, 127.0.0.1)."""
    import uvicorn

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # APScheduler logs every job start/finish and httpx every request URL (query strings
    # included) at INFO; check results are already logged by app.services.monitoring.
    for noisy in ("apscheduler", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = get_settings()
    # One worker only: every worker would run its own scheduler and duplicate checks.
    uvicorn.run(
        "app.main:create_app", factory=True, host=settings.host, port=settings.port, workers=1
    )


if __name__ == "__main__":
    run()
