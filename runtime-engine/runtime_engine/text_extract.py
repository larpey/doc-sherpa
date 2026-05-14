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

from pathlib import Path

from pypdf import PdfReader

# Heuristic: if pypdf extracts fewer than this many characters of text,
# treat the PDF as scanned and fall through to OCR. Scanned pages often
# yield a handful of garbled chars from form labels rendered as text;
# 40 is enough room for that without false-positive-ing on a real born-
# digital doc that just happens to be short.
_OCR_FALLBACK_THRESHOLD_CHARS = 40


class TextExtractionError(Exception):
    """Raised when neither born-digital nor OCR paths could yield text."""


def extract_text(path: Path) -> str:
    """Return the concatenated text of `path`.

    Returns the empty string for PDFs that contain no extractable text
    AND for which OCR is not yet wired. Callers should treat empty as
    "skip classification, route to unclassified."
    """
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

    text = "\n".join(p for p in parts if p)
    if len(text.strip()) >= _OCR_FALLBACK_THRESHOLD_CHARS:
        return text

    # Scanned PDF or near-empty extraction. OCR fallback is the right
    # answer; not wired in this slice. Return what little we have so
    # downstream classification has *something* to work with — but
    # the caller should expect a low-confidence classification.
    return text
