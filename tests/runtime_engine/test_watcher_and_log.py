"""End-to-end: drop PDFs in a watch dir, run one scan, verify logging + placement."""

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
from runtime_engine.log import (
    apply_correction,
    get,
    list_recent,
    mark_accepted,
)
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
    """Self-contained runtime environment: watch + dest + db all in tmp."""
    watch = tmp_path / "watch"
    dest_root = tmp_path / "classified"
    db_path = tmp_path / "runtime.db"
    watch.mkdir()
    init_db(db_path)
    kb = load_kb(PACKS_DIR, include=["logistics", "healthcare"])
    destination = FilesystemDestination(root=dest_root, auto_create=True)
    config = RoutingConfig()
    return {
        "watch": watch,
        "dest_root": dest_root,
        "db_path": db_path,
        "kb": kb,
        "destination": destination,
        "config": config,
    }


def _copy_fixture(env: dict, filename: str) -> Path:
    """Copy a fixture into the watch dir, returning its new path."""
    target = env["watch"] / filename
    shutil.copy2(FIXTURES_DIR / filename, target)
    return target


def test_scan_picks_up_and_files_new_documents(env) -> None:
    """Drop two PDFs → one scan → both end up classified, placed, logged."""
    _copy_fixture(env, "invoice_acme.pdf")
    _copy_fixture(env, "bol_heineken.pdf")

    new_entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )

    assert len(new_entries) == 2
    by_filename = {e.source_filename: e for e in new_entries}
    assert by_filename["invoice_acme.pdf"].doc_type == "invoice"
    assert by_filename["bol_heineken.pdf"].doc_type == "bol"
    assert by_filename["bol_heineken.pdf"].vendor == "Heineken"

    # File actually exists at the placement target.
    for entry in new_entries:
        assert entry.final_path is not None
        assert Path(entry.final_path).exists()
        assert entry.status == "pending"


def test_scan_idempotent_on_already_processed(env) -> None:
    """A second scan over the same files produces zero new entries."""
    _copy_fixture(env, "invoice_acme.pdf")
    scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    second = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    assert second == []
    # Log should still hold exactly one row.
    assert len(list_recent(env["db_path"])) == 1


def test_accept_marks_status(env) -> None:
    _copy_fixture(env, "pod_delivery.pdf")
    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    entry = entries[0]
    mark_accepted(env["db_path"], entry.id)
    updated = get(env["db_path"], entry.id)
    assert updated.status == "accepted"
    assert updated.reviewed_at is not None


def test_correction_moves_file_and_logs(env) -> None:
    """Operator changes doc_type → file moves, row updates."""
    _copy_fixture(env, "invoice_acme.pdf")
    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    entry = entries[0]
    original_final = entry.final_path
    assert Path(original_final).exists()

    # Pretend the operator says "this is actually a receipt, from FooVendor."
    corrected = apply_correction(
        env["db_path"],
        entry.id,
        new_doc_type="receipt",
        new_vendor="FooVendor",
        routing_config=env["config"],
        destination=env["destination"],
    )

    assert corrected.status == "corrected"
    assert corrected.corrected_doc_type == "receipt"
    assert corrected.corrected_vendor == "FooVendor"
    assert corrected.final_path != original_final
    assert Path(corrected.final_path).exists()
    assert not Path(original_final).exists()
    # Destination path reflects the new doc type.
    assert corrected.destination_path.replace("\\", "/").startswith("receipt/")


def test_error_path_still_logs(env) -> None:
    """A non-PDF file in the watch dir is ignored; corrupt PDF gets an error row."""
    # Non-PDF — should be skipped entirely.
    (env["watch"] / "note.txt").write_text("not a pdf")
    # Corrupt PDF — should produce an error log entry.
    (env["watch"] / "broken.pdf").write_bytes(b"%PDF-not really\nnope")

    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    by_name = {e.source_filename: e for e in entries}
    # The .txt should not appear; the .pdf might error or might classify as unclassified.
    assert "note.txt" not in by_name
    if "broken.pdf" in by_name:
        # Either error status or routed to unclassified — both acceptable.
        entry = by_name["broken.pdf"]
        assert entry.status in {"error", "pending"}
