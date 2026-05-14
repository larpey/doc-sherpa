"""Turn operator corrections into overlay rows.

When the operator changes a classification's `doc_type` (and optionally
`vendor`), this module:

1. Re-reads the OCR text from the document
2. Extracts distinctive phrases from it
3. Writes overlay keywords for the *new* doc type
4. Writes the vendor (if supplied) with the new doc type as a likely type

The KB-cache invalidation happens in the caller (`log.apply_correction`
→ this module → caller invalidates). We don't reach across module
boundaries to do it; the wiring layer owns the cache.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import overlay
from .phrase_extract import extract_phrases
from .text_extract import extract_text

logger = logging.getLogger(__name__)

# How many phrases per correction to lift into the overlay. Conservative
# default — we don't want one correction to dominate the rules.
_MAX_PHRASES_PER_CORRECTION = 6


def apply_correction_to_overlay(
    *,
    overlay_db: Path,
    source_text_path: Path | None,
    new_doc_type: str,
    new_vendor: str | None,
    old_doc_type: str | None,
    old_vendor: str | None,
) -> dict[str, int]:
    """Persist learnings from one correction. Returns a summary dict.

    `source_text_path` is the path to the file we should re-extract text
    from — typically the *placed* file (`final_path`), since the source
    in the watch folder may have been deleted by `delete_source_after_place`.
    If text extraction fails or returns empty, we still record vendor
    learnings but skip keyword writes.

    Returns: {'keywords_added': N, 'vendor_updated': 0 or 1}.
    """
    summary = {"keywords_added": 0, "vendor_updated": 0}

    # Make sure overlay tables exist — first-call lazy init handles
    # callers that didn't go through main.py's lifespan (e.g. tests
    # that exercise apply_correction directly).
    overlay.init_overlay(overlay_db)

    # Keyword learnings (only if we can read the text)
    if source_text_path is not None and source_text_path.exists():
        try:
            text = extract_text(source_text_path)
        except Exception:  # noqa: BLE001 — defensive
            logger.exception("learning: could not re-extract text from %s", source_text_path)
            text = ""

        if text.strip():
            phrases = extract_phrases(text, max_phrases=_MAX_PHRASES_PER_CORRECTION)
            for phrase in phrases:
                overlay.upsert_keyword(overlay_db, new_doc_type, phrase)
                summary["keywords_added"] += 1

    # Vendor learnings (no text required)
    if new_vendor:
        # If the OCR text was available, the vendor string as it appears
        # in the doc may differ from the canonical form the operator
        # typed. Upper-cased text is a reasonable alias candidate.
        new_alias = new_vendor.upper() if new_vendor.upper() != new_vendor else None
        overlay.upsert_vendor(
            overlay_db,
            canonical=new_vendor,
            new_alias=new_alias,
            likely_doc_type=new_doc_type,
        )
        summary["vendor_updated"] = 1

    return summary
