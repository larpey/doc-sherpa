"""Local KB overlay — what the system has *learned* from operator corrections.

The classifier reads from a merged view: pack YAML (the ships-with
knowledge) plus this overlay (the per-deployment learnings). Operator
corrections in the review queue write rows here; the next classification
that resembles the corrected doc benefits.

Three tables — keep it boring:
- `overlay_keywords` — (doc_type, phrase, weight, evidence_count)
- `overlay_vendors` — (canonical, aliases_json, likely_doc_types_json)
- `overlay_doc_types` — for doc types the operator introduced that the
  packs didn't ship. Rare; the packs cover the common vocabulary.

The merge function builds a fresh `KnowledgeBase` per call. Cache it at
the dependency-injection layer, invalidate on writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from shared.types import (
    DocTypeDefinition,
    KeywordHint,
    KnowledgeBase,
    VendorDefinition,
)

# Single source of truth for SQLite connection semantics. The overlay
# tables live in the same DB as the classification log; they must share
# the same isolation / FK-on / row-factory settings.
from .db import connect

# Bounds on per-keyword weight. Starting weight is conservative — a single
# correction shouldn't dominate the classifier. Reinforcement pushes it up,
# capped well short of the pack's most-confident keywords (~0.95).
_INITIAL_WEIGHT = 0.40
_WEIGHT_PER_REINFORCEMENT = 0.10
_MAX_WEIGHT = 0.85


_SCHEMA = """
CREATE TABLE IF NOT EXISTS overlay_keywords (
    doc_type        TEXT NOT NULL,
    phrase          TEXT NOT NULL,
    weight          REAL NOT NULL,
    evidence_count  INTEGER NOT NULL DEFAULT 1,
    last_reinforced TEXT NOT NULL,
    PRIMARY KEY (doc_type, phrase)
);

CREATE TABLE IF NOT EXISTS overlay_vendors (
    canonical               TEXT PRIMARY KEY,
    aliases_json            TEXT NOT NULL DEFAULT '[]',
    likely_doc_types_json   TEXT NOT NULL DEFAULT '[]',
    evidence_count          INTEGER NOT NULL DEFAULT 1,
    last_reinforced         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS overlay_doc_types (
    name            TEXT PRIMARY KEY,
    description     TEXT NOT NULL DEFAULT '',
    industries_json TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);
"""


def init_overlay(db_path: Path) -> None:
    """Create the overlay tables. Idempotent. Lives in the same DB as the log."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- keyword writes ----------

def upsert_keyword(db_path: Path, doc_type: str, phrase: str) -> None:
    """Add or reinforce one keyword hint for a doc type.

    First write: weight = `_INITIAL_WEIGHT`, evidence_count = 1.
    Reinforcement: weight += `_WEIGHT_PER_REINFORCEMENT` (capped),
    evidence_count incremented.
    """
    now = _now()
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT weight, evidence_count FROM overlay_keywords WHERE doc_type = ? AND phrase = ?",
            (doc_type, phrase),
        ).fetchone()
        if existing is None:
            conn.execute(
                """
                INSERT INTO overlay_keywords (doc_type, phrase, weight, evidence_count, last_reinforced)
                VALUES (?, ?, ?, 1, ?)
                """,
                (doc_type, phrase, _INITIAL_WEIGHT, now),
            )
        else:
            new_weight = min(existing["weight"] + _WEIGHT_PER_REINFORCEMENT, _MAX_WEIGHT)
            new_count = existing["evidence_count"] + 1
            conn.execute(
                """
                UPDATE overlay_keywords
                SET weight = ?, evidence_count = ?, last_reinforced = ?
                WHERE doc_type = ? AND phrase = ?
                """,
                (new_weight, new_count, now, doc_type, phrase),
            )


# ---------- vendor writes ----------

def upsert_vendor(
    db_path: Path,
    canonical: str,
    *,
    new_alias: str | None = None,
    likely_doc_type: str | None = None,
) -> None:
    """Add a vendor or extend an existing one.

    Idempotent on `canonical`. Aliases and likely_doc_types accumulate
    as sets; nothing is removed by this call.
    """
    now = _now()
    with connect(db_path) as conn:
        existing = conn.execute(
            "SELECT aliases_json, likely_doc_types_json, evidence_count "
            "FROM overlay_vendors WHERE canonical = ?",
            (canonical,),
        ).fetchone()
        if existing is None:
            aliases = [new_alias] if new_alias else []
            likely = [likely_doc_type] if likely_doc_type else []
            conn.execute(
                """
                INSERT INTO overlay_vendors (
                    canonical, aliases_json, likely_doc_types_json,
                    evidence_count, last_reinforced
                ) VALUES (?, ?, ?, 1, ?)
                """,
                (canonical, json.dumps(aliases), json.dumps(likely), now),
            )
        else:
            aliases = set(json.loads(existing["aliases_json"]))
            likely = set(json.loads(existing["likely_doc_types_json"]))
            if new_alias:
                aliases.add(new_alias)
            if likely_doc_type:
                likely.add(likely_doc_type)
            conn.execute(
                """
                UPDATE overlay_vendors
                SET aliases_json = ?, likely_doc_types_json = ?,
                    evidence_count = ?, last_reinforced = ?
                WHERE canonical = ?
                """,
                (
                    json.dumps(sorted(aliases)),
                    json.dumps(sorted(likely)),
                    existing["evidence_count"] + 1,
                    now,
                    canonical,
                ),
            )


# ---------- read + merge ----------

@dataclass(frozen=True, slots=True)
class _LearnedKeyword:
    doc_type: str
    phrase: str
    weight: float


def list_learned_keywords(db_path: Path) -> list[_LearnedKeyword]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT doc_type, phrase, weight FROM overlay_keywords"
        ).fetchall()
    return [_LearnedKeyword(r["doc_type"], r["phrase"], float(r["weight"])) for r in rows]


def list_learned_vendors(db_path: Path) -> list[VendorDefinition]:
    """Reify overlay vendors as VendorDefinition objects."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT canonical, aliases_json, likely_doc_types_json
            FROM overlay_vendors
            """
        ).fetchall()
    return [
        VendorDefinition(
            canonical=r["canonical"],
            aliases=tuple(json.loads(r["aliases_json"])),
            industries=(),
            likely_doc_types=tuple(json.loads(r["likely_doc_types_json"])),
        )
        for r in rows
    ]


def merge_overlay_into(kb: KnowledgeBase, db_path: Path) -> KnowledgeBase:
    """Mutate `kb` in place to include learned keywords + vendors. Returns it.

    Keyword merge: for each `(doc_type, phrase, weight)`, append a
    `KeywordHint` to that doc type's keywords if the phrase isn't
    already present (pack-defined keywords always win — we only ADD).

    Vendor merge: if the vendor isn't in the KB, add it. If it is,
    extend its aliases and likely_doc_types with the learned values.
    """
    learned = list_learned_keywords(db_path)
    by_doc_type: dict[str, list[_LearnedKeyword]] = {}
    for lk in learned:
        by_doc_type.setdefault(lk.doc_type, []).append(lk)

    for doc_type_name, learned_keywords in by_doc_type.items():
        existing = kb.doc_types.get(doc_type_name)
        if existing is None:
            # Operator-introduced doc type that no pack covers — create
            # a minimal definition. Empty fields (extraction is opt-in).
            kb.doc_types[doc_type_name] = DocTypeDefinition(
                name=doc_type_name,
                description="(learned from corrections)",
                industries=(),
                keywords=tuple(KeywordHint(lk.phrase, lk.weight) for lk in learned_keywords),
                fields=(),
            )
        else:
            existing_phrases = {k.phrase.upper() for k in existing.keywords}
            additions = tuple(
                KeywordHint(lk.phrase, lk.weight)
                for lk in learned_keywords
                if lk.phrase.upper() not in existing_phrases
            )
            if additions:
                kb.doc_types[doc_type_name] = DocTypeDefinition(
                    name=existing.name,
                    description=existing.description,
                    industries=existing.industries,
                    keywords=existing.keywords + additions,
                    fields=existing.fields,
                )

    for vendor in list_learned_vendors(db_path):
        existing_v = kb.vendors.get(vendor.canonical)
        if existing_v is None:
            kb.vendors[vendor.canonical] = vendor
        else:
            merged_aliases = tuple(sorted(set(existing_v.aliases) | set(vendor.aliases)))
            merged_likely = tuple(
                sorted(set(existing_v.likely_doc_types) | set(vendor.likely_doc_types))
            )
            kb.vendors[vendor.canonical] = VendorDefinition(
                canonical=existing_v.canonical,
                aliases=merged_aliases,
                industries=existing_v.industries,
                likely_doc_types=merged_likely,
                field_extractors=existing_v.field_extractors,
            )

    kb.sources.append("overlay")
    return kb
