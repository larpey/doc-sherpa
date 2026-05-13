"""FastAPI app entry point.

Run with:
    uvicorn training_service.main:app --app-dir training-service --reload --port 8001

This module is intentionally thin: wire the lifespan, mount the router,
expose `app`. Anything resembling logic belongs in a sibling module so it
stays unit-testable without spinning up the whole app.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import sessions as sessions_module
from .db import init_db
from .routes import router
from .settings import settings

logger = logging.getLogger(__name__)

# How often the cleanup sweep runs. 1 hour is a good trade for a 24h TTL —
# expired sessions linger at most one extra hour, and the work is cheap.
_CLEANUP_INTERVAL_SECONDS = 60 * 60


async def _cleanup_loop() -> None:
    """Periodic cleanup: delete expired sessions and their files.

    Runs forever until cancelled by the lifespan shutdown. Errors are
    logged and swallowed — a transient SQLite or filesystem hiccup must
    not take down the whole service.
    """
    while True:
        try:
            deleted = sessions_module.cleanup_expired(
                settings.database_path,
                settings.storage_dir,
                settings.session_ttl_hours,
            )
            if deleted:
                logger.info("cleanup: removed %d expired session(s)", len(deleted))
        except Exception:  # noqa: BLE001 — defensive boundary
            logger.exception("cleanup: sweep failed; will retry next interval")
        await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks. Initialize the DB and start the cleanup loop."""
    init_db(settings.database_path)
    logger.info("training-service ready · db=%s storage=%s", settings.database_path, settings.storage_dir)

    cleanup_task = asyncio.create_task(_cleanup_loop(), name="cleanup-loop")
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Doc Sherpa — training service",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
