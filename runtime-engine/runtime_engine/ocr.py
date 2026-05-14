"""OCR for scanned PDFs via ocrmypdf + tesseract.

`text_extract.py` calls into this module when `pypdf` returns no text —
i.e. the PDF is rasterized pages, not born-digital. OCR is the slow path
(seconds per page vs. milliseconds for born-digital), so we never call it
speculatively.

Tesseract has to be installed on the host. ocrmypdf will surface a clear
error if it's missing; we let it propagate as `OCRError` so the caller
logs once and falls through to "unclassified" rather than crashing.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Raised when OCR fails for any reason (missing tesseract, timeout, etc.)."""


def is_available() -> bool:
    """Whether ocrmypdf + tesseract are both importable / on PATH."""
    try:
        import ocrmypdf  # noqa: F401
    except ImportError:
        return False
    # `ocrmypdf` checks tesseract at run time; we don't pre-verify here
    # because the import-check on tesseract paths is platform-dependent
    # and the error we get at `ocr()` time is already clear.
    return True


def ocr_pdf(source: Path, *, timeout_seconds: int = 120) -> str:
    """Run OCR on a PDF and return the extracted text.

    Writes the OCR'd PDF to a temp file (we throw away the rendered copy
    — the goal here is just the text), then reads text back with pypdf.

    Args:
        source: Input PDF.
        timeout_seconds: Hard cap on OCR runtime. A pathological multi-
            hundred-page PDF would otherwise hold the watcher hostage.

    Raises:
        OCRError: When ocrmypdf is missing, tesseract is missing, or the
            run exceeds the timeout / fails for any reason.
    """
    try:
        import ocrmypdf
    except ImportError as exc:
        raise OCRError("ocrmypdf is not installed") from exc

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        out_path = Path(tmp.name)

    try:
        ocrmypdf.ocr(
            input_file=str(source),
            output_file=str(out_path),
            # `skip-text` skips pages that already have text and OCRs the
            # rest. Better than `force-ocr` because some PDFs are mixed
            # born-digital + scanned and we don't want to re-OCR what's
            # already extractable.
            skip_text=True,
            # Hard cap so a pathological multi-thousand-page PDF can't
            # hold the watcher hostage. Without this, ocrmypdf will work
            # for as long as it takes.
            timeout=timeout_seconds,
            # Suppress reportlab telemetry noise on stderr.
            progress_bar=False,
            quiet=True,
        )
    except Exception as exc:  # noqa: BLE001 — ocrmypdf raises a wide variety of exceptions
        out_path.unlink(missing_ok=True)
        raise OCRError(f"ocrmypdf failed: {exc}") from exc

    try:
        from pypdf import PdfReader

        reader = PdfReader(str(out_path))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(p for p in parts if p)
    finally:
        out_path.unlink(missing_ok=True)
