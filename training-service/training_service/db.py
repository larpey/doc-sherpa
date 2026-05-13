"""SQLite connection + schema for the training service.

One file per service is the deliberate trade — the runtime engine will have
its own SQLite schema for the review queue. They never share a database, so
they don't share a `db.py` either.

Why stdlib `sqlite3` and not SQLAlchemy: the schema is three tables, queries
are short, and the surface area we'd buy with an ORM is smaller than the
dependency cost. If we ever need migrations beyond `CREATE TABLE IF NOT
EXISTS`, revisit.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,           -- ISO 8601 UTC
    status      TEXT NOT NULL DEFAULT 'open',  -- open | trained | exported
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    original_filename   TEXT NOT NULL,
    stored_path         TEXT NOT NULL,    -- absolute path on disk
    size_bytes          INTEGER NOT NULL,
    content_type        TEXT,
    uploaded_at         TEXT NOT NULL,
    ocr_text            TEXT              -- populated by phase 1 deliverable 4
);

CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);

CREATE TABLE IF NOT EXISTS labels (
    document_id     TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    doc_type        TEXT NOT NULL,
    vendor          TEXT,
    identifier      TEXT,   -- invoice or BOL number
    customer        TEXT,
    labeled_at      TEXT NOT NULL
);
"""


def init_db(db_path: Path) -> None:
    """Create the SQLite database file and apply the schema.

    Idempotent. Called once at app startup from the FastAPI lifespan. The
    parent directory is created if missing — the POC ships with no
    pre-existing storage dir.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with sensible defaults (Row factory, FK on).

    Used by every CRUD module. Always close via the context manager so the
    connection is returned to the OS even on exception — SQLite is fine
    with this pattern; it's not a pooled resource.
    """
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
