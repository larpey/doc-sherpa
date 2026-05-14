"""Extract text from a PDF.

Two paths, chosen by inspection: born-digital PDFs have text directly
in the PDF stream — `pypdf` pulls it out instantly. Scanned PDFs have
only rasterized pages — we need OCR (`ocrmypdf` + tesseract).

This module owns the *decision* and the born-digital path. The OCR path
is a thin shell-out that lives in `ocr.py` once we wire it. Today the
OCR path raises `NotImplementedError`; tests that only need born-digital
extraction don't trigger it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pypdf import PdfReader

from . import ocr as ocr_module

logger = logging.getLogger(__name__)


def _ocr_engine_setting() -> str:
    """Read the engine preference from settings without importing eagerly.

    Imported lazily because `settings` pulls in pydantic-settings which
    is fine but adds latency to the cold import of this module.
    """
    from .settings import settings

    return settings.ocr_engine

# Heuristic: if pypdf extracts fewer than this many characters of text,
# treat the PDF as scanned and fall through to OCR. Scanned pages often
# yield a handful of garbled chars from form labels rendered as text;
# 40 is enough room for that without false-positive-ing on a real born-
# digital doc that just happens to be short.
_OCR_FALLBACK_THRESHOLD_CHARS = 40


class TextExtractionError(Exception):
    """Raised when neither born-digital nor OCR paths could yield text."""


def _extract_born_digital(path: Path) -> str:
    """Pull text out of a born-digital PDF (no OCR). May return empty."""
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise TextExtractionError(f"could not open {path}: {exc}") from exc

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            # A single bad page should not poison the whole document.
            continue
    return "\n".join(p for p in parts if p)


def extract_text(path: Path) -> str:
    """Return the concatenated text of `path`, OCR'ing if needed.

    Born-digital first (fast). If that yields too little, fall through to
    OCR via tesseract. If OCR is unavailable or fails, returns whatever
    born-digital extraction produced (possibly empty) — callers handle
    empty as "route to unclassified."
    """
    born_digital = _extract_born_digital(path)
    if len(born_digital.strip()) >= _OCR_FALLBACK_THRESHOLD_CHARS:
        return born_digital

    if not ocr_module.is_available():
        # OCR not installed. Log once at WARNING; the user already saw
        # this in the install docs but it bears repeating in logs the
        # day they hit their first scanned doc.
        logger.warning(
            "OCR not available for %s — install tesseract + ocrmypdf to "
            "classify scanned PDFs; falling back to empty text",
            path.name,
        )
        return born_digital

    try:
        ocred = ocr_module.ocr_pdf(path, engine=_ocr_engine_setting())
    except ocr_module.OCRError as exc:
        logger.warning("OCR failed on %s: %s", path.name, exc)
        return born_digital

    # Pick the longer of the two — sometimes OCR misreads a born-digital
    # page; sometimes pypdf misses text on a mixed PDF.
    return ocred if len(ocred.strip()) > len(born_digital.strip()) else born_digital
