"""Classify representative fake-OCR'd documents against the shipped KB."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime_engine.classifier import classify
from runtime_engine.kb import load_kb

PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"


@pytest.fixture
def kb_logistics():
    return load_kb(PACKS_DIR, include=["logistics"])


@pytest.fixture
def kb_healthcare():
    return load_kb(PACKS_DIR, include=["healthcare"])


# Fake-OCR fixtures: only the salient text — real OCR has more noise.

INVOICE_TEXT = """
ACME WHOLESALE
INVOICE
Invoice #: INV-12345
Bill To: ABC Distributing
Amount Due: $1,234.56
Payment Terms: Net 30
"""

BOL_TEXT = """
HEINEKEN USA
BILL OF LADING
BOL Number: BOL-987654
Consignee: KDC Warehouse
SCAC: HEIN
Seal Number: 22817
"""

POD_TEXT = """
PROOF OF DELIVERY
Customer Signature: ___________
Cases Received: 24
Delivery Date: 03/15/2026
"""

PRESCRIPTION_TEXT = """
Rx
Patient: J. Doe
Drug: Lisinopril 10mg
SIG: Take 1 tablet daily
Refills: 5
NPI: 1234567890
DEA #: AB1234567
"""


def test_invoice_classification(kb_logistics) -> None:
    """A clear invoice scores cleanly as invoice."""
    result = classify(INVOICE_TEXT, kb_logistics)
    assert result.doc_type == "invoice"
    assert result.confidence > 0.5
    assert any(s.kind == "keyword" for s in result.signals)


def test_bol_classification_with_vendor(kb_logistics) -> None:
    """A BOL from Heineken should classify as bol AND identify the vendor."""
    result = classify(BOL_TEXT, kb_logistics)
    assert result.doc_type == "bol"
    assert result.vendor == "Heineken"
    assert result.confidence > 0.5
    # Vendor signal should be present
    assert any(s.kind == "vendor" for s in result.signals)


def test_pod_classification(kb_logistics) -> None:
    result = classify(POD_TEXT, kb_logistics)
    assert result.doc_type == "pod"
    assert result.confidence > 0.5


def test_prescription_classification(kb_healthcare) -> None:
    """Multi-industry KB: prescription wins against base doc types."""
    result = classify(PRESCRIPTION_TEXT, kb_healthcare)
    assert result.doc_type == "prescription"


def test_empty_text_returns_none(kb_logistics) -> None:
    result = classify("", kb_logistics)
    assert result.doc_type is None
    assert result.confidence == 0.0


def test_no_keywords_returns_none(kb_logistics) -> None:
    """Text with no recognizable signals → no classification."""
    result = classify("the quick brown fox jumps over the lazy dog", kb_logistics)
    assert result.doc_type is None
    assert result.confidence == 0.0


def test_ambiguous_text_lower_confidence(kb_logistics) -> None:
    """When multiple doc types match similarly, confidence drops."""
    # "INVOICE" + "STATEMENT" both present → near-tie
    ambiguous = "INVOICE  STATEMENT  AMOUNT DUE  ACCOUNT STATEMENT  BEGINNING BALANCE"
    result = classify(ambiguous, kb_logistics)
    assert result.doc_type is not None
    # Runner-up should also be set, and confidence should not be runaway-high
    assert result.runner_up is not None
    assert result.confidence < 0.9
