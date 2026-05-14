"""Watch a folder for new PDFs and process each through the pipeline.

Polling-based — `watchdog` is a fine library, but for a POC that runs on
Windows / SMB shares (where file events are flaky), a periodic scan is
simpler and just as effective. The scan interval is configurable.

Two functions:
- `scan_once` — synchronous pass over the watch dir; processes any new
  PDFs. Returns the list of LogEntries for what was processed. Used in
  tests.
- `run_loop` — async coroutine that calls `scan_once` forever. Started
  from the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from shared.types import KnowledgeBase

from . import log as log_module
from .pipeline import process_document
from .router import RoutingConfig

logger = logging.getLogger(__name__)


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _is_stable(path: Path, threshold_seconds: float) -> bool:
    """Return True if `path`'s size is unchanged across the threshold window.

    Guards against picking up half-written files. Two sizes taken
    `threshold` apart — same → stable; different → still being written,
    skip this cycle and try again next pass.
    """
    if not path.exists():
        return False
    try:
        size_before = path.stat().st_size
    except OSError:
        return False
    time.sleep(threshold_seconds)
    try:
        size_after = path.stat().st_size
    except OSError:
        return False
    return size_before == size_after and size_before > 0


def _already_processed(db_path: Path, source: Path) -> bool:
    return log_module.get_by_source(db_path, str(source)) is not None


def scan_once(
    *,
    watch_dir: Path,
    db_path: Path,
    kb: KnowledgeBase,
    routing_config: RoutingConfig,
    destination,  # DocumentDestination
    stability_seconds: float = 0.0,
    delete_source_after_place: bool = False,
    ai_fallback=None,
) -> list[log_module.LogEntry]:
    """One pass over `watch_dir`. Returns the entries created this pass.

    Files already in the log are skipped. Half-written files (size still
    changing) are skipped this pass and picked up next time.
    """
    if not watch_dir.exists():
        return []

    new_entries: list[log_module.LogEntry] = []
    for path in sorted(watch_dir.iterdir()):
        if not _is_pdf(path):
            continue
        if _already_processed(db_path, path):
            continue
        if stability_seconds > 0 and not _is_stable(path, stability_seconds):
            continue

        result = process_document(path, kb, routing_config, destination, ai_fallback=ai_fallback)
        entry = log_module.record_result(db_path, result)
        new_entries.append(entry)

        if delete_source_after_place and result.final_path and not result.error:
            try:
                path.unlink()
            except OSError:
                logger.warning("could not delete source file after placement: %s", path)

    return new_entries


async def run_loop(
    *,
    watch_dir: Path,
    db_path: Path,
    kb: KnowledgeBase,
    routing_config: RoutingConfig,
    destination,
    poll_interval_seconds: float,
    stability_seconds: float,
    delete_source_after_place: bool,
    ai_fallback=None,
) -> None:
    """Run `scan_once` forever, sleeping `poll_interval_seconds` between passes.

    Errors during a pass are logged and swallowed; one bad file should
    not take down the watcher. The watcher itself is cancelled by the
    FastAPI lifespan on shutdown.
    """
    while True:
        try:
            entries = scan_once(
                watch_dir=watch_dir,
                db_path=db_path,
                kb=kb,
                routing_config=routing_config,
                destination=destination,
                stability_seconds=stability_seconds,
                delete_source_after_place=delete_source_after_place,
                ai_fallback=ai_fallback,
            )
            if entries:
                logger.info("watcher: processed %d new document(s)", len(entries))
        except Exception:  # noqa: BLE001 — defensive boundary
            logger.exception("watcher: scan failed; will retry next interval")
        await asyncio.sleep(poll_interval_seconds)
