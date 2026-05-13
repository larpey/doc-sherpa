"""DocumentDestination Protocol: where a classified document gets placed.

Concrete implementations live in `plugins/document_destination/`. POC order
is filesystem first, then S3, then Google Drive, then SharePoint.

The Protocol is intentionally narrow. Anything that looks like "compute a
destination path" or "rename based on metadata" belongs in the routing
layer, not in the plugin — the plugin's job is the *transport*, not the
*decision*. Keeping the surface narrow is the only way to add a third or
fourth destination without each one re-implementing its own variation of
path-templating.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DocumentDestination(Protocol):
    """A place a classified document can be written to."""

    name: str
    """Stable identifier used in `classifier.yaml` to select this plugin
    (e.g. `"filesystem"`, `"s3"`). Must be unique within its category."""

    def place(self, source: Path, destination_path: str) -> str:
        """Write `source` to `destination_path` within this destination.

        Args:
            source: A local file path. The plugin reads from here.
            destination_path: A POSIX-style relative path *within* the
                destination's root (e.g. `"BOLs/2025-03/Heineken_12345.pdf"`).
                The plugin is responsible for any provider-specific path
                quoting or folder creation.

        Returns:
            A string identifier the operator can use to locate the placed
            document — a URL for cloud destinations, an absolute filesystem
            path for local, etc. Used in notifications and audit logs.

        Raises:
            DestinationError: If placement fails for any reason the
                operator needs to know about (auth, quota, path conflict).
                Pipeline-level retry logic catches and routes to the review
                queue; the plugin itself does not retry.
        """
        ...


class DestinationError(Exception):
    """Raised by a `DocumentDestination` when placement fails.

    Keep the message operator-readable; this surfaces in the review queue
    UI verbatim. Avoid leaking stack traces or provider-specific noise.
    """
