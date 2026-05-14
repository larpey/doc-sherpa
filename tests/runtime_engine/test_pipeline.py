"""End-to-end pipeline test against the synthetic PDF fixtures.

Drops each generated PDF through `process_document` and asserts:
- The classifier picks the expected doc type
- The router places the file under the expected doc-type folder
- A vendor-overridden fixture (Heineken BOL) also identifies the vendor
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Adjust import path so we can pull from the plugins/ tree.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.document_destination import FilesystemDestination
from runtime_engine.kb import load_kb
from runtime_engine.pipeline import process_document
from runtime_engine.router import RoutingConfig
from tests.fixtures.generate_pdfs import FIXTURES, generate_all

PACKS_DIR = _ROOT / "packs"
FIXTURES_DIR = _ROOT / "tests" / "fixtures" / "synthetic"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures_exist() -> None:
    """Regenerate fixtures if any are missing (CI safety)."""
    needed = [FIXTURES_DIR / f.filename for f in FIXTURES]
    if not all(p.exists() for p in needed):
        generate_all(FIXTURES_DIR)


@pytest.fixture
def kb():
    """KB loaded with all the industry packs the fixtures touch."""
    return load_kb(PACKS_DIR, include=["logistics", "healthcare"])


@pytest.fixture
def destination(tmp_path):
    return FilesystemDestination(root=tmp_path / "classified", auto_create=True)


@pytest.fixture
def config():
    return RoutingConfig()


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda f: f.filename)
def test_pipeline_classifies_each_fixture(fixture, kb, destination, config) -> None:
    """Every fixture lands in the correct doc-type folder."""
    source = FIXTURES_DIR / fixture.filename
    result = process_document(source, kb, config, destination)

    assert result.error is None, f"pipeline error on {fixture.filename}: {result.error}"
    assert result.classification.doc_type == fixture.expected_doc_type, (
        f"{fixture.filename}: expected {fixture.expected_doc_type}, "
        f"got {result.classification.doc_type} "
        f"(signals: {[s.detail for s in result.classification.signals]})"
    )
    assert result.final_path is not None
    # The destination path is relative; it should *start* with the expected doc type.
    normalized = result.destination_path.replace("\\", "/")
    assert normalized.startswith(f"{fixture.expected_doc_type}/"), (
        f"{fixture.filename} routed to {normalized!r}"
    )


def test_heineken_bol_identifies_vendor(kb, destination, config) -> None:
    """Vendor-override fixture: Heineken BOL should set vendor + use PO# extractor."""
    source = FIXTURES_DIR / "bol_heineken.pdf"
    result = process_document(source, kb, config, destination)

    assert result.classification.doc_type == "bol"
    assert result.classification.vendor == "Heineken"
    # The Heineken vendor override extracts BOL number from "PO #" labels.
    assert result.classification.fields.get("bol_number") == "HK-44218"
    # The filename should reflect the vendor.
    assert "Heineken" in result.final_path


def test_unclassified_text_routes_to_unclassified(tmp_path, kb, destination, config) -> None:
    """A PDF with no recognizable signals should land in unclassified/."""
    # Render a one-page PDF with text that doesn't trigger any KB keyword.
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER

    mystery = tmp_path / "mystery.pdf"
    c = canvas.Canvas(str(mystery), pagesize=LETTER)
    c.setFont("Helvetica", 11)
    for i, line in enumerate(
        ["the quick brown fox", "jumps over the lazy dog", "nothing classifiable here"]
    ):
        c.drawString(72, 720 - i * 18, line)
    c.save()

    result = process_document(mystery, kb, config, destination)
    assert result.was_unclassified
    assert result.destination_path.replace("\\", "/").startswith("unclassified/")
