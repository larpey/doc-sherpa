"""Tests for the OCR engine dispatcher and parallel watcher.

The real OCR engines (rapidocr, tesseract) aren't exercised here — they
have heavy install footprints and would slow tests. Instead we mock the
underlying engine functions and test the dispatcher's selection logic.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.document_destination import FilesystemDestination
from runtime_engine import ocr as ocr_module
from runtime_engine.db import init_db
from runtime_engine.kb import load_kb
from runtime_engine.overlay import init_overlay
from runtime_engine.router import RoutingConfig
from runtime_engine.watcher import scan_once_async
from tests.fixtures.generate_pdfs import FIXTURES, generate_all

PACKS_DIR = _ROOT / "packs"
FIXTURES_DIR = _ROOT / "tests" / "fixtures" / "synthetic"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures_exist() -> None:
    if not all((FIXTURES_DIR / f.filename).exists() for f in FIXTURES):
        generate_all(FIXTURES_DIR)


# ---------- OCR engine dispatcher ----------

def test_resolve_engine_explicit_choice_must_be_installed() -> None:
    """Asking for rapidocr by name when it's not installed must raise."""
    with patch.object(ocr_module, "_has_rapidocr", return_value=False):
        with pytest.raises(ocr_module.OCRError):
            ocr_module._resolve_engine("rapidocr")


def test_resolve_engine_auto_prefers_rapidocr() -> None:
    """`auto` picks rapidocr if both are available."""
    with patch.object(ocr_module, "_has_rapidocr", return_value=True), \
         patch.object(ocr_module, "_has_tesseract", return_value=True):
        assert ocr_module._resolve_engine("auto") == "rapidocr"


def test_resolve_engine_auto_falls_through_to_tesseract() -> None:
    with patch.object(ocr_module, "_has_rapidocr", return_value=False), \
         patch.object(ocr_module, "_has_tesseract", return_value=True):
        assert ocr_module._resolve_engine("auto") == "tesseract"


def test_resolve_engine_raises_when_nothing_available() -> None:
    with patch.object(ocr_module, "_has_rapidocr", return_value=False), \
         patch.object(ocr_module, "_has_tesseract", return_value=False):
        with pytest.raises(ocr_module.OCRError):
            ocr_module._resolve_engine("auto")


def test_is_available_reflects_engine_presence() -> None:
    with patch.object(ocr_module, "_has_rapidocr", return_value=False), \
         patch.object(ocr_module, "_has_tesseract", return_value=False):
        assert not ocr_module.is_available()
    with patch.object(ocr_module, "_has_rapidocr", return_value=True), \
         patch.object(ocr_module, "_has_tesseract", return_value=False):
        assert ocr_module.is_available()


# ---------- parallel watcher ----------

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


@pytest.mark.asyncio
async def test_parallel_watcher_processes_all_candidates(env) -> None:
    """Three PDFs, parallelism=3 → all three are processed in one pass."""
    for name in ["invoice_acme.pdf", "bol_heineken.pdf", "pod_delivery.pdf"]:
        shutil.copy2(FIXTURES_DIR / name, env["watch"] / name)

    entries = await scan_once_async(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
        parallelism=3,
    )
    assert len(entries) == 3
    by_filename = {e.source_filename: e for e in entries}
    assert by_filename["invoice_acme.pdf"].doc_type == "invoice"
    assert by_filename["bol_heineken.pdf"].doc_type == "bol"
    assert by_filename["pod_delivery.pdf"].doc_type == "pod"


@pytest.mark.asyncio
async def test_parallel_watcher_respects_semaphore(env) -> None:
    """Two parallel workers see overlapping execution windows.

    We use a sleep-inside-classifier shim. With parallelism=1, total time
    is ~2x sleep duration. With parallelism=2, it should be ~1x.
    """
    for name in ["invoice_acme.pdf", "bol_heineken.pdf"]:
        shutil.copy2(FIXTURES_DIR / name, env["watch"] / name)

    # Patch process_document to inject a delay so we can observe the
    # difference between sequential and parallel execution.
    from runtime_engine import watcher as watcher_mod

    real_process = watcher_mod.process_document

    def slow_process(*args, **kwargs):
        time.sleep(0.25)
        return real_process(*args, **kwargs)

    with patch.object(watcher_mod, "process_document", side_effect=slow_process):
        # Re-prep DB (the first round may have processed before patch)
        init_db(env["db_path"])
        init_overlay(env["db_path"])

        start_serial = time.monotonic()
        await scan_once_async(
            watch_dir=env["watch"],
            db_path=env["db_path"],
            kb=env["kb"],
            routing_config=env["config"],
            destination=env["destination"],
            parallelism=1,
        )
        serial_elapsed = time.monotonic() - start_serial

    # Reset and run parallel
    init_db(env["db_path"])
    init_overlay(env["db_path"])
    for name in ["invoice_acme.pdf", "bol_heineken.pdf"]:
        # Delete the prior placement so dedup doesn't skip
        for p in env["dest_root"].rglob(f"*{name}"):
            p.unlink()

    with patch.object(watcher_mod, "process_document", side_effect=slow_process):
        start_parallel = time.monotonic()
        await scan_once_async(
            watch_dir=env["watch"],
            db_path=env["db_path"],
            kb=env["kb"],
            routing_config=env["config"],
            destination=env["destination"],
            parallelism=4,
        )
        parallel_elapsed = time.monotonic() - start_parallel

    # Parallel pass should be meaningfully faster than serial. Allow a
    # generous margin — CI machines vary. With 0.25s sleep × 2 docs:
    # serial = ~0.5s, parallel = ~0.25s. Demand at least a 1.3x speedup.
    assert serial_elapsed / max(parallel_elapsed, 0.01) > 1.3, (
        f"expected speedup; serial={serial_elapsed:.3f}s "
        f"parallel={parallel_elapsed:.3f}s"
    )
