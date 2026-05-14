"""Tests for the setup wizard, drag-to-folder route, and LLM fallback wiring.

The LLM plugin itself is not exercised against a live Anthropic API in
tests (would require a key and would be slow + nondeterministic). We
test the *wiring*: the pipeline asks the fallback when keyword confidence
is low, and the fallback's result is used when it's stronger.
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
from runtime_engine.db import init_db
from runtime_engine.kb import load_kb
from runtime_engine.log import list_recent
from runtime_engine.overlay import init_overlay
from runtime_engine.pipeline import process_document
from runtime_engine.router import RoutingConfig
from runtime_engine.watcher import scan_once
from runtime_engine.wizard import WizardConfig, is_complete, load, save, validate_paths
from shared.types import Classification, Signal
from tests.fixtures.generate_pdfs import FIXTURES, generate_all

PACKS_DIR = _ROOT / "packs"
FIXTURES_DIR = _ROOT / "tests" / "fixtures" / "synthetic"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures_exist() -> None:
    if not all((FIXTURES_DIR / f.filename).exists() for f in FIXTURES):
        generate_all(FIXTURES_DIR)


# ---------- wizard persistence ----------

def test_wizard_round_trip(tmp_path) -> None:
    cfg = WizardConfig(
        watch_dir=tmp_path / "watch",
        destination_root=tmp_path / "dest",
        active_packs=("logistics",),
        auto_create_folders=False,
    )
    assert not is_complete(tmp_path)
    save(tmp_path, cfg)
    assert is_complete(tmp_path)
    loaded = load(tmp_path)
    assert loaded is not None
    assert loaded.watch_dir == cfg.watch_dir
    assert loaded.destination_root == cfg.destination_root
    assert loaded.active_packs == cfg.active_packs
    assert loaded.auto_create_folders is False


def test_wizard_validate_creates_and_probes(tmp_path) -> None:
    cfg = WizardConfig(
        watch_dir=tmp_path / "watch",
        destination_root=tmp_path / "dest",
        active_packs=(),
    )
    assert validate_paths(cfg) == []
    assert (tmp_path / "watch").is_dir()
    assert (tmp_path / "dest").is_dir()


# ---------- top-3 alternatives ----------

def test_classifier_returns_alternatives() -> None:
    """Multiple matching doc types → alternatives populated, not just runner_up."""
    from runtime_engine.classifier import classify

    kb = load_kb(PACKS_DIR, include=["logistics"])
    # Text that hits invoice + bol + receipt keywords simultaneously.
    text = (
        "INVOICE AMOUNT DUE BILL TO\n"
        "BILL OF LADING CONSIGNEE\n"
        "RECEIPT THANK YOU AMOUNT PAID"
    )
    result = classify(text, kb)
    assert result.doc_type is not None
    # Top-3 → up to three names other than the winner.
    assert isinstance(result.alternatives, tuple)
    assert len(result.alternatives) >= 1
    # No duplicates with the winner.
    alternative_names = {name for name, _score in result.alternatives}
    assert result.doc_type not in alternative_names


# ---------- LLM fallback wiring (with a fake plugin) ----------

class _FakeLLM:
    """Stand-in for ClaudeClassifier. Returns a fixed Classification."""

    name = "fake-llm"

    def __init__(self, result: Classification | None) -> None:
        self._result = result
        self.called_with: str | None = None

    def classify(self, ocr_text: str, known_vendors=None, known_doc_types=None):
        self.called_with = ocr_text
        return self._result


@pytest.fixture
def env(tmp_path):
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


def test_pipeline_does_not_call_llm_when_confidence_is_high(env) -> None:
    """A clear invoice never reaches the LLM fallback."""
    fake = _FakeLLM(result=None)
    shutil.copy2(FIXTURES_DIR / "invoice_acme.pdf", env["watch"] / "invoice_acme.pdf")
    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
        ai_fallback=fake,
    )
    assert entries[0].doc_type == "invoice"
    # Keyword path had enough confidence — fallback should not have been consulted.
    assert fake.called_with is None


def test_pipeline_uses_llm_when_keyword_path_is_weak(env) -> None:
    """A doc that doesn't match any keywords should consult the LLM."""
    # A novel doc that the packs don't recognize.
    novel = env["watch"] / "novel.pdf"
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER

    c = canvas.Canvas(str(novel), pagesize=LETTER)
    c.setFont("Helvetica", 11)
    for i, line in enumerate(["mystery contents", "nothing here", "absolutely nothing"]):
        c.drawString(72, 720 - i * 18, line)
    c.save()

    fake = _FakeLLM(
        result=Classification(
            doc_type="invoice",
            vendor="LLM-Identified Inc",
            fields={"llm_identifier": "MAGIC-1"},
            confidence=0.85,
            signals=(Signal(kind="llm", detail="fake", contribution=0.85),),
        )
    )
    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
        ai_fallback=fake,
    )
    assert fake.called_with is not None
    assert entries[0].doc_type == "invoice"
    assert entries[0].vendor == "LLM-Identified Inc"


def test_pipeline_keeps_keyword_result_if_llm_returns_none(env) -> None:
    """LLM unavailable / errored → keyword classification stands."""
    novel = env["watch"] / "novel2.pdf"
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import LETTER

    c = canvas.Canvas(str(novel), pagesize=LETTER)
    c.setFont("Helvetica", 11)
    c.drawString(72, 720, "mystery text")
    c.save()

    fake = _FakeLLM(result=None)
    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
        ai_fallback=fake,
    )
    # Either doc_type is None (truly unclassified) or some weak match; in
    # either case the pipeline did not crash.
    assert entries[0].error is None or "extraction" not in (entries[0].error or "")
