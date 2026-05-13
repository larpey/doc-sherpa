"""DocumentIngest Protocol: how the runtime engine receives new documents.

Concrete implementations live in `plugins/document_ingest/`. POC order:
watch folder first, HTTP upload API second.

The Protocol is push-based: the plugin runs its own loop (watchdog observer,
HTTP server, polling pull from a queue) and calls back into the pipeline
when a new document arrives. This keeps the pipeline runner agnostic to the
ingest mechanism — it just registers a callback and waits.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

IngestCallback = Callable[[Path], None]
"""Callback the ingest plugin calls when a new document arrives.

The argument is a local path the pipeline can read. The plugin is
responsible for moving any remote/streamed bytes onto local disk before
invoking the callback — the pipeline expects a file, not a stream.
"""


@runtime_checkable
class DocumentIngest(Protocol):
    """A source of new documents for the runtime engine."""

    name: str
    """Stable identifier (e.g. `"watch-folder"`, `"http-upload"`)."""

    def start(self, on_document: IngestCallback) -> None:
        """Begin producing documents. Calls `on_document(path)` per arrival.

        The plugin is responsible for its own lifecycle (background thread,
        async loop, etc.). `start` may block or return immediately depending
        on the implementation; either is acceptable. The pipeline runner
        runs each ingest plugin in its own thread/task as a defensive
        boundary against blocking implementations.
        """
        ...

    def stop(self) -> None:
        """Stop producing documents. Idempotent. Used on graceful shutdown."""
        ...
