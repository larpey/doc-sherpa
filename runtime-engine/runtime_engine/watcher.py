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


def _collect_candidates(
    watch_dir: Path,
    db_path: Path,
    stability_seconds: float,
) -> list[tuple[Path, str | None]]:
    """Return PDFs that are ready to process, paired with their content hash.

    Filters out non-PDFs, already-processed source paths, content-hash
    duplicates (both against the log AND within this batch — two
    identical files in the same pass shouldn't both classify), and
    files that haven't reached stability.
    """
    if not watch_dir.exists():
        return []
    candidates: list[tuple[Path, str | None]] = []
    seen_hashes_this_pass: set[str] = set()
    for path in sorted(watch_dir.iterdir()):
        if not _is_pdf(path):
            continue
        if _already_processed(db_path, path):
            continue
        if stability_seconds > 0 and not _is_stable(path, stability_seconds):
            continue
        try:
            content_hash = file_sha256(path)
        except OSError:
            content_hash = None
        if content_hash:
            if _content_already_processed(db_path, content_hash):
                logger.info("watcher: skipping duplicate content for %s", path.name)
                continue
            if content_hash in seen_hashes_this_pass:
                logger.info(
                    "watcher: skipping in-batch duplicate content for %s", path.name
                )
                continue
            seen_hashes_this_pass.add(content_hash)
        candidates.append((path, content_hash))
    return candidates


def _process_one(
    path: Path,
    content_hash: str | None,
    *,
    db_path: Path,
    kb: KnowledgeBase,
    routing_config: RoutingConfig,
    destination,
    ai_fallback,
    auto_confirm_threshold: float,
    delete_source_after_place: bool,
) -> log_module.LogEntry:
    """Run the pipeline on one document and record the result. CPU-bound."""
    result = process_document(path, kb, routing_config, destination, ai_fallback=ai_fallback)
    entry = log_module.record_result(
        db_path,
        result,
        content_sha256=content_hash,
        auto_confirm_threshold=auto_confirm_threshold,
    )
    if delete_source_after_place and result.final_path and not result.error:
        try:
            path.unlink()
        except OSError:
            logger.warning("could not delete source file after placement: %s", path)
    return entry


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
    """Synchronous one-pass scan — processes candidates sequentially.

    Kept synchronous for tests and for the parallelism=1 (default) case
    where the overhead of asyncio.to_thread isn't worth it. `run_loop`
    uses `scan_once_async` for parallel mode.
    """
    candidates = _collect_candidates(watch_dir, db_path, stability_seconds)
    new_entries: list[log_module.LogEntry] = []
    for path, content_hash in candidates:
        entry = _process_one(
            path,
            content_hash,
            db_path=db_path,
            kb=kb,
            routing_config=routing_config,
            destination=destination,
            ai_fallback=ai_fallback,
            auto_confirm_threshold=auto_confirm_threshold,
            delete_source_after_place=delete_source_after_place,
        )
        new_entries.append(entry)
    return new_entries


async def scan_once_async(
    *,
    watch_dir: Path,
    db_path: Path,
    kb: KnowledgeBase,
    routing_config: RoutingConfig,
    destination,
    stability_seconds: float = 0.0,
    delete_source_after_place: bool = False,
    ai_fallback=None,
    auto_confirm_threshold: float = 1.1,
    parallelism: int = 1,
) -> list[log_module.LogEntry]:
    """Async one-pass scan — processes candidates up to `parallelism` at a time.

    Each document is run in a worker thread via `asyncio.to_thread`, so
    the watcher coroutine itself never blocks. A semaphore caps the
    in-flight count so we don't oversubscribe the CPU.
    """
    candidates = _collect_candidates(watch_dir, db_path, stability_seconds)
    if not candidates:
        return []

    semaphore = asyncio.Semaphore(max(1, parallelism))

    async def _worker(path: Path, content_hash: str | None) -> log_module.LogEntry:
        async with semaphore:
            return await asyncio.to_thread(
                _process_one,
                path,
                content_hash,
                db_path=db_path,
                kb=kb,
                routing_config=routing_config,
                destination=destination,
                ai_fallback=ai_fallback,
                auto_confirm_threshold=auto_confirm_threshold,
                delete_source_after_place=delete_source_after_place,
            )

    return await asyncio.gather(*[_worker(p, h) for p, h in candidates])


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
    parallelism: int = 1,
) -> None:
    """Run `scan_once_async` forever, sleeping `poll_interval_seconds` between passes.

    Errors during a pass are logged and swallowed; one bad file should
    not take down the watcher. The watcher itself is cancelled by the
    FastAPI lifespan on shutdown.

    `parallelism` controls how many documents OCR + classify simultaneously.
    """
    while True:
        try:
            entries = await scan_once_async(
                watch_dir=watch_dir,
                db_path=db_path,
                kb=kb,
                routing_config=routing_config,
                destination=destination,
                stability_seconds=stability_seconds,
                delete_source_after_place=delete_source_after_place,
                ai_fallback=ai_fallback,
                auto_confirm_threshold=auto_confirm_threshold,
                parallelism=parallelism,
            )
            if entries:
                logger.info("watcher: processed %d new document(s)", len(entries))
        except Exception:  # noqa: BLE001 — defensive boundary
            logger.exception("watcher: scan failed; will retry next interval")
        await asyncio.sleep(poll_interval_seconds)
