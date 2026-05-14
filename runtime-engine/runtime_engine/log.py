"""Persist + query classifications. CRUD for the runtime engine's log.

A classification row is born when a PDF flows through the pipeline.
Its lifecycle: `pending` → `accepted` (operator confirmed) or
`corrected` (operator changed the doc_type / vendor, file was moved).

We keep error rows around too: a PDF that failed text extraction still
gets a row with `status='error'`. The operator sees it in the inbox and
can manually re-process or delete.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shared.types import Classification, Signal

from .db import connect
from .pipeline import ProcessResult
from .router import RoutingConfig, render_destination


@dataclass(frozen=True, slots=True)
class LogEntry:
    """Reified row from the `classifications` table."""

    id: str
    source_path: str
    source_filename: str
    content_sha256: str | None
    final_path: str | None
    destination_path: str | None
    doc_type: str | None
    vendor: str | None
    confidence: float
    fields: dict[str, str]
    signals: list[Signal]
    was_unclassified: bool
    error: str | None
    processed_at: datetime
    status: str
    reviewed_at: datetime | None
    corrected_doc_type: str | None
    corrected_vendor: str | None
    operator_note: str | None


def _row_to_entry(row: sqlite3.Row) -> LogEntry:
    return LogEntry(
        id=row["id"],
        source_path=row["source_path"],
        source_filename=row["source_filename"],
        content_sha256=row["content_sha256"] if "content_sha256" in row.keys() else None,
        final_path=row["final_path"],
        destination_path=row["destination_path"],
        doc_type=row["doc_type"],
        vendor=row["vendor"],
        confidence=float(row["confidence"]),
        fields=json.loads(row["fields_json"]),
        signals=[Signal(**s) for s in json.loads(row["signals_json"])],
        was_unclassified=bool(row["was_unclassified"]),
        error=row["error"],
        processed_at=datetime.fromisoformat(row["processed_at"]),
        status=row["status"],
        reviewed_at=datetime.fromisoformat(row["reviewed_at"]) if row["reviewed_at"] else None,
        corrected_doc_type=row["corrected_doc_type"],
        corrected_vendor=row["corrected_vendor"],
        operator_note=row["operator_note"],
    )


def _serialize_signals(signals: tuple[Signal, ...]) -> str:
    return json.dumps(
        [{"kind": s.kind, "detail": s.detail, "contribution": s.contribution} for s in signals]
    )


_MAX_ERROR_LEN = 300  # truncate stored error strings to avoid leaking long paths / library noise


def record_result(
    db_path: Path,
    result: ProcessResult,
    *,
    content_sha256: str | None = None,
    auto_confirm_threshold: float = 1.1,  # > 1.0 = effectively never auto-confirm
) -> LogEntry:
    """Insert a fresh row for a freshly-processed document.

    Idempotent on `source_path`: if a row exists for the same source, we
    do not overwrite (the operator may have already corrected it). The
    watcher's dedup check normally prevents this branch from firing.

    `auto_confirm_threshold`: if the classification's confidence meets
    or exceeds this, the row is born `accepted` instead of `pending` so
    the operator doesn't see it. Set to a value > 1 to disable.
    """
    classification: Classification = result.classification
    now = datetime.now(timezone.utc)
    # Truncate error strings — ocrmypdf and pypdf both produce long
    # exception messages that include full temp-file paths and internal
    # library state. Surface the gist, not the noise.
    error_msg = result.error
    if error_msg and len(error_msg) > _MAX_ERROR_LEN:
        error_msg = error_msg[: _MAX_ERROR_LEN - 3] + "..."
    is_auto_confirmed = (
        not result.error
        and classification.doc_type is not None
        and classification.confidence >= auto_confirm_threshold
    )
    if result.error:
        status = "error"
        reviewed_at = None
    elif is_auto_confirmed:
        status = "accepted"
        reviewed_at = now
    else:
        status = "pending"
        reviewed_at = None

    entry = LogEntry(
        id=uuid.uuid4().hex,
        source_path=str(result.source_path),
        source_filename=result.source_path.name,
        content_sha256=content_sha256,
        final_path=result.final_path,
        destination_path=result.destination_path,
        doc_type=classification.doc_type,
        vendor=classification.vendor,
        confidence=classification.confidence,
        fields=dict(classification.fields),
        signals=list(classification.signals),
        was_unclassified=result.was_unclassified,
        error=error_msg,
        processed_at=now,
        status=status,
        reviewed_at=reviewed_at,
        corrected_doc_type=None,
        corrected_vendor=None,
        operator_note=None,
    )

    with connect(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO classifications (
                    id, source_path, source_filename, content_sha256,
                    final_path, destination_path,
                    doc_type, vendor, confidence, fields_json, signals_json,
                    was_unclassified, error, processed_at, status, reviewed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.source_path,
                    entry.source_filename,
                    entry.content_sha256,
                    entry.final_path,
                    entry.destination_path,
                    entry.doc_type,
                    entry.vendor,
                    entry.confidence,
                    json.dumps(entry.fields),
                    _serialize_signals(tuple(entry.signals)),
                    int(entry.was_unclassified),
                    entry.error,
                    entry.processed_at.isoformat(),
                    entry.status,
                    entry.reviewed_at.isoformat() if entry.reviewed_at else None,
                ),
            )
        except sqlite3.IntegrityError:
            # Already processed — return the existing row instead.
            existing = get_by_source(db_path, entry.source_path)
            if existing is not None:
                return existing
            raise
    return entry


def list_recent(
    db_path: Path,
    limit: int = 50,
    status: str | None = None,
    offset: int = 0,
) -> list[LogEntry]:
    """List rows newest first, with optional status filter + offset for pagination."""
    query = "SELECT * FROM classifications"
    params: list = []
    if status is not None:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY processed_at DESC LIMIT ? OFFSET ?"
    params.append(limit)
    params.append(offset)
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_entry(r) for r in rows]


def get(db_path: Path, entry_id: str) -> LogEntry | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM classifications WHERE id = ?", (entry_id,)
        ).fetchone()
    return _row_to_entry(row) if row else None


def get_by_source(db_path: Path, source_path: str) -> LogEntry | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM classifications WHERE source_path = ?", (source_path,)
        ).fetchone()
    return _row_to_entry(row) if row else None


def get_by_content_hash(db_path: Path, content_sha256: str) -> LogEntry | None:
    """Look up an existing entry by file-content hash. Used for dedup.

    Returns the first row that matches — if the same content has been
    seen multiple times (which would itself be a bug if dedup is on),
    we'll consistently return the earliest one.
    """
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM classifications WHERE content_sha256 = ? LIMIT 1",
            (content_sha256,),
        ).fetchone()
    return _row_to_entry(row) if row else None


def delete_entry(db_path: Path, entry_id: str, *, also_remove_file: bool = False) -> bool:
    """Remove a row. Optionally also delete the placed file.

    Returns True on success, False if the row didn't exist.
    """
    entry = get(db_path, entry_id)
    if entry is None:
        return False
    if also_remove_file and entry.final_path:
        try:
            Path(entry.final_path).unlink()
        except OSError:
            pass
    with connect(db_path) as conn:
        conn.execute("DELETE FROM classifications WHERE id = ?", (entry_id,))
    return True


def status_counts(db_path: Path) -> dict[str, int]:
    """Aggregate row counts per status. Used by the /stats endpoint."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM classifications GROUP BY status"
        ).fetchall()
    return {row["status"]: int(row["n"]) for row in rows}


def average_confidence(db_path: Path) -> float:
    """Return the average classification confidence across all rows. 0.0 if empty."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT AVG(confidence) AS avg_conf FROM classifications"
        ).fetchone()
    return float(row["avg_conf"]) if row and row["avg_conf"] is not None else 0.0


def mark_accepted(db_path: Path, entry_id: str) -> None:
    """The operator confirmed the classification — record it. No file move."""
    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE classifications SET status = 'accepted', reviewed_at = ? WHERE id = ?",
            (now, entry_id),
        )


def apply_correction(
    db_path: Path,
    entry_id: str,
    *,
    new_doc_type: str,
    new_vendor: str | None,
    routing_config: RoutingConfig,
    destination,  # DocumentDestination
    note: str | None = None,
    learn: bool = True,
) -> LogEntry:
    """Move the file to the corrected location, update the row, and teach.

    We re-render the destination using the corrected classification, ask
    the plugin to place a copy at the new location, and delete the file
    at the old location only after the new write succeeds (atomicity:
    one file always exists). The original `source_path` is left alone
    — the operator may want to re-process it later.

    If `learn` is True (default), also writes overlay rows so the next
    similar doc benefits from this correction.
    """
    entry = get(db_path, entry_id)
    if entry is None:
        raise KeyError(entry_id)
    if entry.final_path is None:
        raise ValueError(f"entry {entry_id!r} has no final_path; nothing to move")

    # Build a corrected Classification and render the new destination.
    corrected = Classification(
        doc_type=new_doc_type,
        vendor=new_vendor,
        fields=entry.fields,
        confidence=entry.confidence,
        signals=tuple(entry.signals),
    )
    new_dest_path = render_destination(corrected, Path(entry.source_path), routing_config)
    new_final_path = destination.place(Path(entry.final_path), new_dest_path)

    old_final_path = entry.final_path
    # Delete the old file only if the new path is different — placing to
    # the same path with a collision-rename would give a different
    # `new_final_path` and we want to clean up the original.
    if new_final_path != old_final_path:
        try:
            Path(old_final_path).unlink()
        except OSError:
            # If we can't delete, leave it — duplicate is recoverable.
            pass

    now = datetime.now(timezone.utc).isoformat()
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE classifications SET
                status              = 'corrected',
                corrected_doc_type  = ?,
                corrected_vendor    = ?,
                destination_path    = ?,
                final_path          = ?,
                operator_note       = ?,
                reviewed_at         = ?
            WHERE id = ?
            """,
            (new_doc_type, new_vendor, new_dest_path, new_final_path, note, now, entry_id),
        )

    if learn:
        # Late import: log.py is imported by deps.py indirectly; the
        # learning module needs neither, but circular import paranoia is
        # cheaper than the dependency archaeology to prove it's safe.
        from . import learning as learning_module
        learning_module.apply_correction_to_overlay(
            overlay_db=db_path,
            source_text_path=Path(new_final_path),
            new_doc_type=new_doc_type,
            new_vendor=new_vendor,
            old_doc_type=entry.doc_type,
            old_vendor=entry.vendor,
        )

    return get(db_path, entry_id)  # type: ignore[return-value]
