"""Load + merge the shipped packs. Exercises both YAML parsing and merge."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime_engine.kb import PackLoadError, load_kb

PACKS_DIR = Path(__file__).resolve().parents[2] / "packs"


def test_base_only_loads() -> None:
    """The base pack alone covers the universal doc types."""
    kb = load_kb(PACKS_DIR)
    assert "invoice" in kb.doc_types
    assert "receipt" in kb.doc_types
    assert "statement" in kb.doc_types
    assert "purchase_order" in kb.doc_types
    assert "contract" in kb.doc_types
    assert kb.sources == ["base"]
    assert not kb.vendors  # base ships no vendors


def test_logistics_pack_merges() -> None:
    """Adding the logistics pack brings BOL/POD + seed vendors."""
    kb = load_kb(PACKS_DIR, include=["logistics"])
    assert "bol" in kb.doc_types
    assert "pod" in kb.doc_types
    assert "invoice" in kb.doc_types  # base still present
    assert "Heineken" in kb.vendors
    assert "MOLSON COORS" in kb.vendors["Molson Coors"].aliases


def test_healthcare_pack_merges() -> None:
    kb = load_kb(PACKS_DIR, include=["healthcare"])
    assert "prescription" in kb.doc_types
    assert "lab_result" in kb.doc_types
    assert "insurance_claim" in kb.doc_types


def test_missing_pack_raises() -> None:
    with pytest.raises(PackLoadError):
        load_kb(PACKS_DIR, include=["nonexistent"])


def test_doctype_definition_shape() -> None:
    """Spot-check that one doc type has the expected sub-structure."""
    kb = load_kb(PACKS_DIR, include=["logistics"])
    bol = kb.doc_types["bol"]
    assert bol.description
    assert any(k.phrase == "BILL OF LADING" for k in bol.keywords)
    bol_number = next(f for f in bol.fields if f.name == "bol_number")
    assert bol_number.required
    assert "BOL #" in bol_number.extractor.labels


def test_vendor_override_extractor() -> None:
    """Vendor entries can specify per-field extractor overrides."""
    kb = load_kb(PACKS_DIR, include=["logistics"])
    heineken = kb.vendors["Heineken"]
    assert "bol.bol_number" in heineken.field_extractors
    spec = heineken.field_extractors["bol.bol_number"]
    assert spec.kind == "labeled_value"
    assert "PO #" in spec.labels
