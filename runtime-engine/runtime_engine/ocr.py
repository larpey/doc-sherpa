"""OCR engine layer — engine-agnostic dispatcher with multiple backends.

Two engines today, picked by `settings.ocr_engine`:

- **rapidocr** (preferred default) — ONNX-runtime-based. Multi-threaded
  per call (uses all CPU cores), no system deps, ~100 MB install.
  Same recognition models as PaddleOCR.
- **tesseract** — single-threaded, mature, requires the OS-level
  tesseract binary on PATH. Falls back automatically if rapidocr is
  not installed.

`auto` picks rapidocr if importable, else tesseract.

The dispatcher returns a flat string of recognized text. Callers don't
care which engine produced it.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Raised when OCR fails for any reason."""


# ---------- engine availability checks ----------

def _has_rapidocr() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        import pypdfium2  # noqa: F401
    except ImportError:
        return False
    return True


def _has_tesseract() -> bool:
    try:
        import ocrmypdf  # noqa: F401
    except ImportError:
        return False
    return True


def is_available() -> bool:
    """Whether *any* OCR engine is wired up."""
    return _has_rapidocr() or _has_tesseract()


def _resolve_engine(preferred: str) -> str:
    """Pick a real engine name from a preference string.

    `preferred` is one of `auto`, `rapidocr`, `tesseract`. Returns the
    resolved name or raises `OCRError` if nothing is available.
    """
    if preferred == "rapidocr":
        if _has_rapidocr():
            return "rapidocr"
        raise OCRError("rapidocr requested but not installed")
    if preferred == "tesseract":
        if _has_tesseract():
            return "tesseract"
        raise OCRError("tesseract requested but ocrmypdf is not installed")
    # auto
    if _has_rapidocr():
        return "rapidocr"
    if _has_tesseract():
        return "tesseract"
    raise OCRError("no OCR engine available — install rapidocr-onnxruntime or ocrmypdf")


# ---------- engine: RapidOCR (default) ----------

_rapidocr_instance = None


def _get_rapidocr():
    """Lazy-construct the RapidOCR instance. One per process — cheap to share."""
    global _rapidocr_instance
    if _rapidocr_instance is None:
        from rapidocr_onnxruntime import RapidOCR

        _rapidocr_instance = RapidOCR()
    return _rapidocr_instance


def _ocr_with_rapidocr(source: Path) -> str:
    """Render each page of the PDF to a raster image, OCR with RapidOCR."""
    import pypdfium2

    ocr = _get_rapidocr()
    doc = pypdfium2.PdfDocument(str(source))
    parts: list[str] = []
    try:
        for page in doc:
            # 200 DPI is the standard OCR-quality render — high enough for
            # accurate recognition without burning time on 600 DPI scans.
            bitmap = page.render(scale=200 / 72)
            pil_image = bitmap.to_pil()
            result, _elapsed = ocr(pil_image)
            if not result:
                continue
            page_text = "\n".join(item[1] for item in result if len(item) >= 2 and item[1])
            if page_text:
                parts.append(page_text)
    finally:
        doc.close()
    return "\n".join(parts)


# ---------- engine: tesseract (fallback / opt-in) ----------

def _ocr_with_tesseract(source: Path, timeout_seconds: int) -> str:
    """Run ocrmypdf, then pypdf-extract the resulting PDF's text."""
    import ocrmypdf
    from pypdf import PdfReader

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        out_path = Path(tmp.name)

    try:
        ocrmypdf.ocr(
            input_file=str(source),
            output_file=str(out_path),
            skip_text=True,
            timeout=timeout_seconds,
            progress_bar=False,
            quiet=True,
        )
        reader = PdfReader(str(out_path))
        parts: list[str] = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(p for p in parts if p)
    except Exception as exc:  # noqa: BLE001
        raise OCRError(f"ocrmypdf failed: {exc}") from exc
    finally:
        out_path.unlink(missing_ok=True)


# ---------- public dispatcher ----------

def ocr_pdf(source: Path, *, engine: str = "auto", timeout_seconds: int = 120) -> str:
    """Run OCR on `source` and return the concatenated recognized text.

    Args:
        source: PDF to OCR.
        engine: `auto` | `rapidocr` | `tesseract`. Default `auto` picks
            the best available.
        timeout_seconds: Hard cap on tesseract (rapidocr is fast enough
            we don't bother). A multi-thousand-page PDF could otherwise
            hold the watcher hostage.

    Returns:
        Text extracted from all pages, newline-joined.

    Raises:
        OCRError: When no engine is available or the chosen engine fails.
    """
    resolved = _resolve_engine(engine)
    if resolved == "rapidocr":
        return _ocr_with_rapidocr(source)
    return _ocr_with_tesseract(source, timeout_seconds)
