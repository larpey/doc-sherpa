"""Training session CRUD + lifecycle.

A session is a customer's single training run: 20–30 uploaded documents +
their labels + (eventually) the synthesized classifier.yaml. POC assumption
is one user per service instance, so sessions are anonymous — there is no
session-to-user mapping table.

Cleanup is here rather than in its own module because cleanup operates on
sessions; the responsibility ("session lifecycle") covers both.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import connect


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    created_at: datetime
    status: str
    notes: str | None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        status=row["status"],
        notes=row["notes"],
    )


def create_session(db_path: Path) -> Session:
    """Create a new training session and return it.

    IDs are UUID4 hex strings (32 chars) — URL-safe, no quoting concerns in
    HTML form actions. A growing-integer ID would be slightly nicer in logs
    but leaks volume metrics; not worth it.
    """
    new_id = uuid.uuid4().hex
    created_at = _now_iso()
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sessions (id, created_at, status, notes) VALUES (?, ?, 'open', NULL)",
            (new_id, created_at),
        )
    return Session(id=new_id, created_at=datetime.fromisoformat(created_at), status="open", notes=None)


def get_session(db_path: Path, session_id: str) -> Session | None:
    """Return the session with this ID, or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, created_at, status, notes FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    return _row_to_session(row) if row else None


def list_sessions(db_path: Path, limit: int = 50) -> list[Session]:
    """Return the most recent sessions. Used by the homepage."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, created_at, status, notes FROM sessions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def mark_status(db_path: Path, session_id: str, status: str) -> None:
    """Update the session status. POC vocabulary: 'open' | 'trained' | 'exported'."""
    with connect(db_path) as conn:
        conn.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))


def cleanup_expired(db_path: Path, storage_dir: Path, ttl_hours: int) -> list[str]:
    """Delete sessions and their files older than `ttl_hours`. Returns deleted IDs.

    Phase 1 ships with this defined but not yet scheduled — the FastAPI
    lifespan should call this from a periodic background task. Doing it
    inline on every request would be cheap (SQLite) but couples request
    latency to filesystem walks; we accept the slight complexity of a
    background task in exchange.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=ttl_hours)).isoformat()
    with connect(db_path) as conn:
        expired = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM sessions WHERE created_at < ?", (cutoff,)
            ).fetchall()
        ]
        for session_id in expired:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    # Filesystem cleanup happens after DB commit so a crash mid-cleanup
    # leaves orphaned files (recoverable) rather than orphaned DB rows
    # pointing at deleted files (confusing).
    for session_id in expired:
        session_dir = storage_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)
    return expired
