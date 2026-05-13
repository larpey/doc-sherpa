"""Document upload + storage.

Documents are stored on disk under `<storage_dir>/<session_id>/<doc_id>.pdf`;
metadata lives in the `documents` table. The filesystem layout means the
filesystem and DB can be reconciled with a simple directory walk —
important when a crash or partial cleanup leaves them out of sync.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from .db import connect

ALLOWED_CONTENT_TYPES = frozenset({"application/pdf"})


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    session_id: str
    original_filename: str
    stored_path: Path
    size_bytes: int
    content_type: str | None
    uploaded_at: datetime
    ocr_text: str | None


def _row_to_document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        session_id=row["session_id"],
        original_filename=row["original_filename"],
        stored_path=Path(row["stored_path"]),
        size_bytes=row["size_bytes"],
        content_type=row["content_type"],
        uploaded_at=datetime.fromisoformat(row["uploaded_at"]),
        ocr_text=row["ocr_text"],
    )


class UploadRejected(Exception):
    """Raised when an upload fails validation (size, content-type, cap)."""


def save_document(
    *,
    db_path: Path,
    storage_dir: Path,
    session_id: str,
    original_filename: str,
    content_type: str | None,
    source: BinaryIO,
    max_size_bytes: int,
) -> Document:
    """Persist one uploaded PDF to disk + DB.

    The upload is streamed in 1MB chunks and aborted if it exceeds
    `max_size_bytes` — we never buffer the whole file in memory and we
    never trust the client's `Content-Length` header. If the stream exceeds
    the cap, we delete the partial file and raise `UploadRejected`.
    """
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise UploadRejected(
            f"Only PDF uploads are accepted (got content-type {content_type!r})."
        )

    doc_id = uuid.uuid4().hex
    session_dir = storage_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    stored_path = session_dir / f"{doc_id}.pdf"

    written = 0
    chunk_size = 1024 * 1024  # 1 MiB
    with stored_path.open("wb") as out:
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            written += len(chunk)
            if written > max_size_bytes:
                out.close()
                stored_path.unlink(missing_ok=True)
                raise UploadRejected(
                    f"File exceeds {max_size_bytes // (1024 * 1024)} MiB upload cap."
                )
            out.write(chunk)

    uploaded_at = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO documents (
                id, session_id, original_filename, stored_path,
                size_bytes, content_type, uploaded_at, ocr_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                doc_id,
                session_id,
                original_filename,
                str(stored_path),
                written,
                content_type,
                uploaded_at,
            ),
        )

    return Document(
        id=doc_id,
        session_id=session_id,
        original_filename=original_filename,
        stored_path=stored_path,
        size_bytes=written,
        content_type=content_type,
        uploaded_at=datetime.fromisoformat(uploaded_at),
        ocr_text=None,
    )


def list_documents(db_path: Path, session_id: str) -> list[Document]:
    """Return documents in a session, oldest first (upload order)."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, session_id, original_filename, stored_path, size_bytes,
                   content_type, uploaded_at, ocr_text
            FROM documents
            WHERE session_id = ?
            ORDER BY uploaded_at ASC
            """,
            (session_id,),
        ).fetchall()
    return [_row_to_document(r) for r in rows]


def count_documents(db_path: Path, session_id: str) -> int:
    """Return the document count for a session. Used to enforce the per-session cap."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["n"])
