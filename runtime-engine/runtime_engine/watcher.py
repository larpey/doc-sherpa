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
from .hashing import file_sha256
from .pipeline import process_document
from .router import RoutingConfig

logger = logging.getLogger(__name__)


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _is_stable(path: Path, threshold_seconds: float) -> bool:
    """Return True if `path` has been untouched for at least `threshold_seconds`.

    Guards against picking up half-written files. Uses mtime *age*
    rather than a "size-now vs size-after-sleep" check so the watcher
    doesn't block the event loop sleeping inside the scan pass.

    A file whose mtime is older than `now - threshold` is treated as
    stable. A still-being-written file's mtime is constantly bumped, so
    it remains unstable until the writer finishes.
    """
    if not path.exists():
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_size <= 0:
        return False
    age_seconds = time.time() - stat.st_mtime
    return age_seconds >= threshold_seconds


def _already_processed(db_path: Path, source: Path) -> bool:
    return log_module.get_by_source(db_path, str(source)) is not None


def _content_already_processed(db_path: Path, content_hash: str) -> bool:
    return log_module.get_by_content_hash(db_path, content_hash) is not None


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
    auto_confirm_threshold: float = 1.1,
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

        # Content-hash dedup: same bytes under a different filename
        # shouldn't be re-classified. We hash before pipelining so we
        # avoid the OCR / LLM cost on duplicates.
        try:
            content_hash = file_sha256(path)
        except OSError:
            content_hash = None
        if content_hash and _content_already_processed(db_path, content_hash):
            logger.info("watcher: skipping duplicate content for %s", path.name)
            continue

        result = process_document(path, kb, routing_config, destination, ai_fallback=ai_fallback)
        entry = log_module.record_result(
            db_path,
            result,
            content_sha256=content_hash,
            auto_confirm_threshold=auto_confirm_threshold,
        )
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
    auto_confirm_threshold: float = 1.1,
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
                auto_confirm_threshold=auto_confirm_threshold,
            )
            if entries:
                logger.info("watcher: processed %d new document(s)", len(entries))
        except Exception:  # noqa: BLE001 — defensive boundary
            logger.exception("watcher: scan failed; will retry next interval")
        await asyncio.sleep(poll_interval_seconds)
