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


def record_result(db_path: Path, result: ProcessResult) -> LogEntry:
    """Insert a fresh row for a freshly-processed document.

    Idempotent on `source_path`: if a row exists for the same source, we
    do not overwrite (the operator may have already corrected it). The
    watcher's dedup check normally prevents this branch from firing.
    """
    classification: Classification = result.classification
    entry = LogEntry(
        id=uuid.uuid4().hex,
        source_path=str(result.source_path),
        source_filename=result.source_path.name,
        final_path=result.final_path,
        destination_path=result.destination_path,
        doc_type=classification.doc_type,
        vendor=classification.vendor,
        confidence=classification.confidence,
        fields=dict(classification.fields),
        signals=list(classification.signals),
        was_unclassified=result.was_unclassified,
        error=result.error,
        processed_at=datetime.now(timezone.utc),
        status="error" if result.error else "pending",
        reviewed_at=None,
        corrected_doc_type=None,
        corrected_vendor=None,
        operator_note=None,
    )

    with connect(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO classifications (
                    id, source_path, source_filename, final_path, destination_path,
                    doc_type, vendor, confidence, fields_json, signals_json,
                    was_unclassified, error, processed_at, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.source_path,
                    entry.source_filename,
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
                ),
            )
        except sqlite3.IntegrityError:
            # Already processed — return the existing row instead.
            existing = get_by_source(db_path, entry.source_path)
            if existing is not None:
                return existing
            raise
    return entry


def list_recent(db_path: Path, limit: int = 50, status: str | None = None) -> list[LogEntry]:
    """List the most recent rows, newest first.

    `status` filters by lifecycle state if provided ('pending',
    'accepted', 'corrected', 'error').
    """
    query = "SELECT * FROM classifications"
    params: list = []
    if status is not None:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY processed_at DESC LIMIT ?"
    params.append(limit)
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
) -> LogEntry:
    """Move the file to the corrected location and update the row.

    We re-render the destination using the corrected classification, ask
    the plugin to place a copy at the new location, and delete the file
    at the old location only after the new write succeeds (atomicity:
    one file always exists). The original `source_path` is left alone
    — the operator may want to re-process it later.
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

    # Delete the old file only if the new path is different — placing to
    # the same path with a collision-rename would give a different
    # `new_final_path` and we want to clean up the original.
    if new_final_path != entry.final_path:
        try:
            Path(entry.final_path).unlink()
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

    return get(db_path, entry_id)  # type: ignore[return-value]
