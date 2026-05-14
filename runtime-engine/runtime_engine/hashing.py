"""SHA256 content hashing for documents.

Used to detect when the same PDF arrives under a different filename so
we don't double-process it. A path-based dedup misses this (same content,
different name = two rows); a hash-based dedup catches it.

Computed on file content, not metadata. Streamed in chunks so a 100MB
scan doesn't load into memory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_BYTES = 1024 * 1024  # 1 MiB


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of the file's content."""
    h = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(_CHUNK_BYTES)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
