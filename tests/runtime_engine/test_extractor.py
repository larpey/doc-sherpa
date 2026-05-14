"""Compile extractor specs and run them against fake-OCR text."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime_engine.extractor import extract_fields
from runtime_engine.kb import load_kb

PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"


@pytest.fixture
def kb_logistics():
    return load_kb(PACKS_DIR, include=["logistics"])


def test_invoice_field_extraction(kb_logistics) -> None:
    """Invoice number + total pulled from a generic invoice."""
    text = """
    ACME CORP
    INVOICE
    Invoice #: INV-12345
    Amount Due: $1,234.56
    """
    fields = extract_fields(text, kb_logistics, "invoice", vendor=None)
    assert fields.get("invoice_number") == "INV-12345"
    assert "1,234.56" in (fields.get("total") or "")


def test_bol_generic_extraction(kb_logistics) -> None:
    """Generic BOL extractor finds the number after 'BOL Number'."""
    text = "BILL OF LADING\nBOL Number: BOL-987654\nConsignee: KDC"
    fields = extract_fields(text, kb_logistics, "bol", vendor=None)
    assert fields.get("bol_number") == "BOL-987654"


def test_vendor_override_for_heineken(kb_logistics) -> None:
    """Heineken BOLs use 'PO #' for the BOL number, not 'BOL Number'."""
    text = "HEINEKEN USA\nBILL OF LADING\nPO #: HK-44218\nConsignee: KDC"
    fields = extract_fields(text, kb_logistics, "bol", vendor="Heineken")
    assert fields.get("bol_number") == "HK-44218"


def test_shape_length_constraint(kb_logistics) -> None:
    """A captured value that violates min/max length is rejected."""
    # `invoice_number` requires min_length 3. "I-" (2 chars) should not match.
    text = "INVOICE\nInvoice #: I-\nAmount Due: $50"
    fields = extract_fields(text, kb_logistics, "invoice", vendor=None)
    assert fields.get("invoice_number") is None


def test_missing_doc_type_returns_empty(kb_logistics) -> None:
    fields = extract_fields("anything", kb_logistics, "not_a_doc_type", vendor=None)
    assert fields == {}


def test_missing_fields_not_present(kb_logistics) -> None:
    """Fields that don't extract simply aren't in the result dict."""
    text = "INVOICE\n(no number, no total, no date here)"
    fields = extract_fields(text, kb_logistics, "invoice", vendor=None)
    # Required `invoice_number` not in text → key absent, not None
    assert "invoice_number" not in fields
