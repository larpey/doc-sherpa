"""SQLite schema for the runtime engine's classification log.

One table for the POC: `classifications`. Every processed document gets
a row — accepted, corrected, errored, or pending. The inbox UI queries
this; the learning layer (later) reads corrections from it.

Schema kept narrow on purpose. Fields the operator never sees stay in
the JSON columns; we promote them to first-class columns only when we
need to index or query them.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS classifications (
    id                  TEXT PRIMARY KEY,
    source_path         TEXT NOT NULL,
    source_filename     TEXT NOT NULL,
    content_sha256      TEXT,             -- SHA-256 of the source bytes; NULL on legacy rows
    final_path          TEXT,             -- absolute destination path, NULL on error
    destination_path    TEXT,             -- relative path under root
    doc_type            TEXT,
    vendor              TEXT,
    confidence          REAL NOT NULL DEFAULT 0.0,
    fields_json         TEXT NOT NULL DEFAULT '{}',
    signals_json        TEXT NOT NULL DEFAULT '[]',
    was_unclassified    INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    processed_at        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending',   -- pending | accepted | corrected | error
    reviewed_at         TEXT,
    corrected_doc_type  TEXT,
    corrected_vendor    TEXT,
    operator_note       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_classifications_source
    ON classifications(source_path);

CREATE INDEX IF NOT EXISTS idx_classifications_content_hash
    ON classifications(content_sha256);

CREATE INDEX IF NOT EXISTS idx_classifications_status
    ON classifications(status, processed_at DESC);
"""


def _migrate_add_content_hash(conn) -> None:
    """Add `content_sha256` to legacy DBs created before this column existed."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(classifications)").fetchall()]
    if "content_sha256" not in cols:
        conn.execute("ALTER TABLE classifications ADD COLUMN content_sha256 TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_classifications_content_hash "
            "ON classifications(content_sha256)"
        )


def init_db(db_path: Path) -> None:
    """Create the SQLite DB file and apply the schema. Idempotent.

    Also runs lightweight column-add migrations for backward compatibility
    with DBs created by earlier versions of the runtime.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        _migrate_add_content_hash(conn)
        conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection with Row factory + FK enforcement + autocommit."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
