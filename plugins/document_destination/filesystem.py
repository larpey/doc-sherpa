"""Filesystem DocumentDestination — the first concrete destination.

Writes documents under a configured root directory. Auto-creates parent
directories (`mkdir -p`) so first-time classifications never fail on
"folder doesn't exist." Operator can opt out of auto-create via the
runtime config; in that mode the plugin raises so the doc lands in the
review queue instead of being silently misfiled.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from shared.protocols.document_destination import DestinationError


class FilesystemDestination:
    """Copy classified files into a local-filesystem tree under a root.

    The class implements the `DocumentDestination` Protocol structurally
    — no inheritance needed. Tests assert structural conformance via
    `isinstance(x, DocumentDestination)` on the protocol's
    `runtime_checkable` variant.
    """

    name = "filesystem"

    def __init__(self, root: Path, *, auto_create: bool = True) -> None:
        """Configure the plugin.

        Args:
            root: Absolute path to the destination root. Created if missing.
            auto_create: If False, refuse to create any folder under the
                root that doesn't already exist. Used by operators who
                want strict control over folder structure.
        """
        self._root = Path(root).resolve()
        self._auto_create = auto_create
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def place(self, source: Path, destination_path: str) -> str:
        """Copy `source` to `<root>/<destination_path>`.

        `destination_path` is a POSIX-style relative path. We split on
        `/` and rejoin with the host's separator so Windows clients get
        the path they expect.

        Returns the absolute final path as a string — used in
        notifications and audit logs so operators can find the file.
        """
        parts = [p for p in destination_path.split("/") if p]
        if not parts:
            raise DestinationError("empty destination path")

        target = self._root.joinpath(*parts)
        target_parent = target.parent

        if not target_parent.exists():
            if not self._auto_create:
                raise DestinationError(
                    f"target folder does not exist and auto_create is off: {target_parent}"
                )
            target_parent.mkdir(parents=True, exist_ok=True)

        # Avoid clobbering a same-named existing file (e.g. when the same
        # invoice number arrives twice). Append a counter rather than
        # overwriting — the operator deals with deduplication in the
        # review queue, not the plugin.
        final = _avoid_collision(target)

        try:
            shutil.copy2(source, final)
        except OSError as exc:
            raise DestinationError(f"could not write {final}: {exc}") from exc

        return str(final)


def _avoid_collision(path: Path) -> Path:
    """Return `path`, or `path` with `_2`, `_3`, … appended to its stem.

    Linear scan up to a small cap so we don't loop forever on bizarre
    duplicate floods. If we hit the cap, fall back to a timestamped name.
    """
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(2, 1000):
        candidate = path.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
    # 1000 collisions is wildly unusual — degrade to a timestamp suffix.
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return path.with_name(f"{stem}_{stamp}{suffix}")
