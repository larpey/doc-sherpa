"""Learning loop: corrections write overlay rows; future classifications benefit.

These tests build on the watcher+log fixtures but exercise the full
loop: misclassify → correct → re-classify a similar doc → verify the
new classification reflects what was learned.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.document_destination import FilesystemDestination
from runtime_engine.classifier import classify
from runtime_engine.db import init_db
from runtime_engine.kb import load_kb
from runtime_engine.log import apply_correction
from runtime_engine.overlay import (
    init_overlay,
    list_learned_keywords,
    list_learned_vendors,
    merge_overlay_into,
    upsert_keyword,
    upsert_vendor,
)
from runtime_engine.phrase_extract import extract_phrases
from runtime_engine.pipeline import process_document
from runtime_engine.router import RoutingConfig
from runtime_engine.watcher import scan_once
from tests.fixtures.generate_pdfs import FIXTURES, generate_all

PACKS_DIR = _ROOT / "packs"
FIXTURES_DIR = _ROOT / "tests" / "fixtures" / "synthetic"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures_exist() -> None:
    if not all((FIXTURES_DIR / f.filename).exists() for f in FIXTURES):
        generate_all(FIXTURES_DIR)


@pytest.fixture
def env(tmp_path):
    """Self-contained runtime environment with overlay initialized."""
    watch = tmp_path / "watch"
    dest_root = tmp_path / "classified"
    db_path = tmp_path / "runtime.db"
    watch.mkdir()
    init_db(db_path)
    init_overlay(db_path)
    return {
        "watch": watch,
        "dest_root": dest_root,
        "db_path": db_path,
        "kb": load_kb(PACKS_DIR, include=["logistics", "healthcare"]),
        "destination": FilesystemDestination(root=dest_root, auto_create=True),
        "config": RoutingConfig(),
    }


# ---------- phrase extraction sanity ----------

def test_phrase_extract_finds_titles() -> None:
    text = """
    ACME WHOLESALE CO
    INVOICE
    Invoice #: INV-12345
    AMOUNT DUE: $600
    """
    phrases = extract_phrases(text)
    # Multi-word uppercase titles should appear.
    assert any("ACME WHOLESALE" in p for p in phrases)
    assert any("AMOUNT DUE" in p for p in phrases)


def test_phrase_extract_skips_boilerplate() -> None:
    text = "DATE TOTAL PAGE 1 OF 1 INVOICE"
    phrases = extract_phrases(text)
    # "DATE" and "TOTAL" alone shouldn't be promoted as distinctive.
    assert "DATE" not in phrases
    assert "TOTAL" not in phrases


# ---------- overlay primitives ----------

def test_upsert_keyword_then_reinforce(env) -> None:
    upsert_keyword(env["db_path"], "rebate", "REBATE FORM")
    learned = list_learned_keywords(env["db_path"])
    assert any(lk.phrase == "REBATE FORM" and lk.doc_type == "rebate" for lk in learned)
    first_weight = next(lk.weight for lk in learned if lk.phrase == "REBATE FORM")

    upsert_keyword(env["db_path"], "rebate", "REBATE FORM")
    learned2 = list_learned_keywords(env["db_path"])
    second_weight = next(lk.weight for lk in learned2 if lk.phrase == "REBATE FORM")
    assert second_weight > first_weight


def test_upsert_vendor_accumulates_aliases_and_doc_types(env) -> None:
    upsert_vendor(env["db_path"], "NewCo", new_alias="NEWCO INC", likely_doc_type="bol")
    upsert_vendor(env["db_path"], "NewCo", new_alias="NEW CO", likely_doc_type="pod")
    vendors = list_learned_vendors(env["db_path"])
    newco = next(v for v in vendors if v.canonical == "NewCo")
    assert "NEWCO INC" in newco.aliases
    assert "NEW CO" in newco.aliases
    assert set(newco.likely_doc_types) == {"bol", "pod"}


# ---------- merge into pack KB ----------

def test_merge_adds_new_doc_type_when_pack_has_none(env) -> None:
    upsert_keyword(env["db_path"], "rebate", "REBATE FORM")
    upsert_keyword(env["db_path"], "rebate", "VOLUME DISCOUNT")
    merged = merge_overlay_into(env["kb"], env["db_path"])
    assert "rebate" in merged.doc_types
    phrases = {k.phrase for k in merged.doc_types["rebate"].keywords}
    assert {"REBATE FORM", "VOLUME DISCOUNT"} <= phrases


def test_merge_extends_existing_doc_type_keywords(env) -> None:
    """Overlay keywords append to an existing pack doc type's keyword list."""
    upsert_keyword(env["db_path"], "invoice", "CUSTOM PHRASE")
    merged = merge_overlay_into(env["kb"], env["db_path"])
    invoice = merged.doc_types["invoice"]
    phrases = {k.phrase for k in invoice.keywords}
    assert "CUSTOM PHRASE" in phrases
    # Pack keywords still there.
    assert "INVOICE" in phrases


# ---------- end-to-end learning loop ----------

def test_correction_teaches_classifier(env) -> None:
    """Misclassify-then-correct → similar future text gets classified correctly.

    We synthesize a doc whose text doesn't match any pack rules well, then
    correct the classification to a new doc type. After correction, a
    second similar doc should classify to the new type.
    """
    # Step 1: drop a doc whose text barely matches any rule.
    novel = env["watch"] / "novel.pdf"
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER

    novel_lines = [
        "ACME REBATES",
        "VOLUME DISCOUNT NOTICE",
        "Q1 2026 REBATE FORM",
        "Customer: Big Box Co",
        "Amount: $1200",
    ]
    c = canvas.Canvas(str(novel), pagesize=LETTER)
    c.setFont("Helvetica", 11)
    for i, line in enumerate(novel_lines):
        c.drawString(72, 720 - i * 18, line)
    c.save()

    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    assert len(entries) == 1
    entry = entries[0]
    # Pack KB probably misclassifies (or lands in unclassified) — that's the
    # premise of the test. We don't assert what the pre-correction guess was.

    # Step 2: operator corrects to a new doc_type, supplies a vendor.
    apply_correction(
        env["db_path"],
        entry.id,
        new_doc_type="rebate_notice",
        new_vendor="Acme Rebates",
        routing_config=env["config"],
        destination=env["destination"],
    )

    # Step 3: re-build KB with overlay merged and classify a similar text.
    from copy import deepcopy

    overlaid = deepcopy(env["kb"])
    merge_overlay_into(overlaid, env["db_path"])

    similar_text = """
    ACME REBATES
    VOLUME DISCOUNT NOTICE
    Q2 2026 REBATE FORM
    Customer: Other Big Box
    Amount: $800
    """
    result = classify(similar_text, overlaid)
    assert result.doc_type == "rebate_notice"
    assert result.vendor == "Acme Rebates"
    assert result.confidence > 0.0


def test_correction_writes_overlay_rows(env) -> None:
    """A correction should produce overlay keyword + vendor rows."""
    # Use an existing fixture so we don't have to generate one.
    src = env["watch"] / "invoice_acme.pdf"
    shutil.copy2(FIXTURES_DIR / "invoice_acme.pdf", src)

    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    entry = entries[0]

    # Pre-correction: overlay empty.
    assert list_learned_keywords(env["db_path"]) == []
    assert list_learned_vendors(env["db_path"]) == []

    apply_correction(
        env["db_path"],
        entry.id,
        new_doc_type="invoice",  # keep doc type, but supply a vendor
        new_vendor="Acme Wholesale",
        routing_config=env["config"],
        destination=env["destination"],
    )

    learned_kws = list_learned_keywords(env["db_path"])
    learned_vs = list_learned_vendors(env["db_path"])
    # Some keywords should have been recorded for "invoice".
    assert any(lk.doc_type == "invoice" for lk in learned_kws)
    # Vendor recorded.
    assert any(v.canonical == "Acme Wholesale" for v in learned_vs)
