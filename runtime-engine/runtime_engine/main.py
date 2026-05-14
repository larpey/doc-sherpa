"""FastAPI app entry point for the runtime engine.

Run with:
    uvicorn runtime_engine.main:app --app-dir runtime-engine --reload --port 8002

The lifespan starts the watcher loop in the background — it processes
PDFs from `RUNTIME_ENGINE_WATCH_DIR` continuously while the server is
up. Both the inbox UI and the watcher share the same SQLite DB.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import wizard as wizard_module
from .db import init_db
from .deps import get_ai_fallback, get_destination, get_kb, get_routing_config
from .overlay import init_overlay
from .routes import router
from .settings import settings
from .watcher import run_loop

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize storage, load KB, start watcher, hand control to the app."""
    init_db(settings.db_path)
    init_overlay(settings.db_path)

    # If the wizard has been completed in a previous run, apply its
    # config on top of env-var defaults.
    saved = wizard_module.load(settings.db_path.parent)
    if saved is not None:
        settings.watch_dir = saved.watch_dir
        settings.destination_root = saved.destination_root
        settings.active_packs = saved.active_packs
        settings.auto_create_folders = saved.auto_create_folders

    settings.watch_dir.mkdir(parents=True, exist_ok=True)
    settings.destination_root.mkdir(parents=True, exist_ok=True)

    kb = get_kb()
    routing_config = get_routing_config()
    destination = get_destination()
    ai_fallback = get_ai_fallback()

    logger.info(
        "runtime-engine ready · watch=%s root=%s db=%s llm=%s",
        settings.watch_dir, settings.destination_root, settings.db_path,
        "on" if ai_fallback else "off",
    )

    watcher_task = asyncio.create_task(
        run_loop(
            watch_dir=settings.watch_dir,
            db_path=settings.db_path,
            kb=kb,
            routing_config=routing_config,
            destination=destination,
            poll_interval_seconds=settings.poll_interval_seconds,
            stability_seconds=settings.stability_check_seconds,
            delete_source_after_place=settings.delete_source_after_place,
            ai_fallback=ai_fallback,
        ),
        name="watcher",
    )
    try:
        yield
    finally:
        watcher_task.cancel()
        try:
            await watcher_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Doc Sherpa — runtime engine",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)
