"""Operator-supplied labels for training documents.

Labels are upserted (one row per document). The labeling UI does a single
POST with all labels for a session; partial saves are not a use case in
phase 1 — the operator labels in one sitting.

Empty form fields are normalized to None at this boundary so downstream
code (training, validation scoring) doesn't have to guess whether ""
means "missing" or "explicitly blank".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shared.types import DocumentType

from .db import connect
from .documents import Document, list_documents


@dataclass(frozen=True, slots=True)
class StoredLabel:
    """A persisted label row. Mirrors `shared.types.Label` plus its FK + timestamp."""

    document_id: str
    doc_type: DocumentType
    vendor: str | None
    identifier: str | None
    customer: str | None
    labeled_at: datetime


def _normalize(value: str | None) -> str | None:
    """Empty/whitespace-only strings → None. Trim others."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _row_to_label(row: sqlite3.Row) -> StoredLabel:
    return StoredLabel(
        document_id=row["document_id"],
        doc_type=DocumentType(row["doc_type"]),
        vendor=row["vendor"],
        identifier=row["identifier"],
        customer=row["customer"],
        labeled_at=datetime.fromisoformat(row["labeled_at"]),
    )


def upsert_label(
    *,
    db_path: Path,
    document_id: str,
    doc_type: DocumentType,
    vendor: str | None,
    identifier: str | None,
    customer: str | None,
) -> StoredLabel:
    """Insert or replace the label for `document_id`. Returns the persisted row."""
    labeled_at = datetime.now(timezone.utc).isoformat()
    v_vendor = _normalize(vendor)
    v_identifier = _normalize(identifier)
    v_customer = _normalize(customer)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO labels (document_id, doc_type, vendor, identifier, customer, labeled_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                doc_type   = excluded.doc_type,
                vendor     = excluded.vendor,
                identifier = excluded.identifier,
                customer   = excluded.customer,
                labeled_at = excluded.labeled_at
            """,
            (document_id, doc_type.value, v_vendor, v_identifier, v_customer, labeled_at),
        )
    return StoredLabel(
        document_id=document_id,
        doc_type=doc_type,
        vendor=v_vendor,
        identifier=v_identifier,
        customer=v_customer,
        labeled_at=datetime.fromisoformat(labeled_at),
    )


def get_label(db_path: Path, document_id: str) -> StoredLabel | None:
    """Return the label for a document, or None if unlabeled."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT document_id, doc_type, vendor, identifier, customer, labeled_at
            FROM labels WHERE document_id = ?
            """,
            (document_id,),
        ).fetchone()
    return _row_to_label(row) if row else None


def list_documents_with_labels(
    db_path: Path, session_id: str
) -> list[tuple[Document, StoredLabel | None]]:
    """Return `(document, label | None)` pairs for a session, upload order.

    Used by the labeling UI to render the form pre-populated when the
    operator revisits a partially-labeled session.
    """
    docs = list_documents(db_path, session_id)
    pairs: list[tuple[Document, StoredLabel | None]] = []
    for doc in docs:
        pairs.append((doc, get_label(db_path, doc.id)))
    return pairs
