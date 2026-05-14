"""Tests for the round-2 additions: dedup, auto-confirm, auth, stats, pagination."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.document_destination import FilesystemDestination
from runtime_engine.auth import BearerTokenMiddleware
from runtime_engine.db import init_db
from runtime_engine.hashing import file_sha256
from runtime_engine.kb import load_kb
from runtime_engine.log import (
    average_confidence,
    list_recent,
    record_result,
    status_counts,
)
from runtime_engine.overlay import init_overlay
from runtime_engine.pipeline import ProcessResult
from runtime_engine.router import RoutingConfig
from runtime_engine.watcher import scan_once
from shared.types import Classification
from tests.fixtures.generate_pdfs import FIXTURES, generate_all

PACKS_DIR = _ROOT / "packs"
FIXTURES_DIR = _ROOT / "tests" / "fixtures" / "synthetic"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures_exist() -> None:
    if not all((FIXTURES_DIR / f.filename).exists() for f in FIXTURES):
        generate_all(FIXTURES_DIR)


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


# ---------- content hashing ----------

def test_file_sha256_consistent(tmp_path) -> None:
    f = tmp_path / "x.pdf"
    f.write_bytes(b"%PDF-1.4\nhello world\n%%EOF\n")
    h1 = file_sha256(f)
    h2 = file_sha256(f)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_watcher_dedupes_by_content_hash(env) -> None:
    """Same content under two filenames should classify once, not twice."""
    src = FIXTURES_DIR / "invoice_acme.pdf"
    shutil.copy2(src, env["watch"] / "invoice_a.pdf")
    shutil.copy2(src, env["watch"] / "invoice_b_renamed.pdf")  # same content, different name

    entries = scan_once(
        watch_dir=env["watch"],
        db_path=env["db_path"],
        kb=env["kb"],
        routing_config=env["config"],
        destination=env["destination"],
    )
    # Whichever comes first by sort order classifies; the second is dropped.
    assert len(entries) == 1
    rows = list_recent(env["db_path"])
    assert len(rows) == 1


# ---------- auto-confirm ----------

def test_auto_confirm_marks_high_confidence_as_accepted(env, tmp_path) -> None:
    """A classification at or above the threshold lands as 'accepted', not 'pending'."""
    # Forge a high-confidence ProcessResult and persist it.
    src = tmp_path / "fake.pdf"
    src.write_bytes(b"%PDF-1.4 nope %%EOF")
    result = ProcessResult(
        source_path=src,
        classification=Classification(
            doc_type="invoice", vendor=None, fields={}, confidence=0.95, signals=()
        ),
        destination_path="invoice/2026/x.pdf",
        final_path=str(tmp_path / "out.pdf"),
        was_unclassified=False,
        error=None,
    )
    entry = record_result(env["db_path"], result, auto_confirm_threshold=0.85)
    assert entry.status == "accepted"
    assert entry.reviewed_at is not None


def test_auto_confirm_leaves_low_confidence_pending(env, tmp_path) -> None:
    src = tmp_path / "fake.pdf"
    src.write_bytes(b"%PDF-1.4 nope %%EOF")
    result = ProcessResult(
        source_path=src,
        classification=Classification(
            doc_type="invoice", vendor=None, fields={}, confidence=0.50, signals=()
        ),
        destination_path="invoice/2026/x.pdf",
        final_path=str(tmp_path / "out.pdf"),
        was_unclassified=False,
        error=None,
    )
    entry = record_result(env["db_path"], result, auto_confirm_threshold=0.85)
    assert entry.status == "pending"
    assert entry.reviewed_at is None


# ---------- bearer auth ----------

def test_auth_middleware_passes_health_without_token() -> None:
    """Even with auth on, /healthz is reachable (probes shouldn't have tokens)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/healthz")
    def hz() -> dict:
        return {"ok": True}

    @app.get("/secret")
    def secret() -> dict:
        return {"sensitive": True}

    app.add_middleware(BearerTokenMiddleware, expected_token="s3cr3t")
    client = TestClient(app)

    assert client.get("/healthz").status_code == 200
    assert client.get("/secret").status_code == 401
    assert client.get("/secret", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/secret", headers={"Authorization": "Bearer s3cr3t"}).status_code == 200


def test_auth_off_is_pass_through() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/secret")
    def secret() -> dict:
        return {"sensitive": True}

    app.add_middleware(BearerTokenMiddleware, expected_token=None)
    client = TestClient(app)
    assert client.get("/secret").status_code == 200


# ---------- stats + pagination helpers in log.py ----------

def test_status_counts_returns_empty_dict_on_fresh_db(env) -> None:
    assert status_counts(env["db_path"]) == {}


def test_average_confidence_is_zero_on_fresh_db(env) -> None:
    assert average_confidence(env["db_path"]) == 0.0


def test_pagination_offset(env, tmp_path) -> None:
    """Inserting 3 rows and paginating by 2 returns 2 + 1."""
    for i in range(3):
        src = tmp_path / f"f{i}.pdf"
        src.write_bytes(b"%PDF-1.4 nope %%EOF")
        record_result(
            env["db_path"],
            ProcessResult(
                source_path=src,
                classification=Classification(
                    doc_type="invoice", vendor=None, fields={}, confidence=0.4, signals=()
                ),
                destination_path=f"invoice/x{i}.pdf",
                final_path=str(tmp_path / f"out{i}.pdf"),
                was_unclassified=False,
                error=None,
            ),
        )
        time.sleep(0.001)  # ensure distinct processed_at

    page1 = list_recent(env["db_path"], limit=2, offset=0)
    page2 = list_recent(env["db_path"], limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1


# ---------- pack hot-reload mtime detection ----------

def test_pack_mtime_triggers_kb_reload(env, tmp_path, monkeypatch) -> None:
    """Touching a pack file should cause get_kb() to rebuild on next call."""
    monkeypatch.setenv("RUNTIME_ENGINE_DB_PATH", str(env["db_path"]))

    import importlib
    import os

    import runtime_engine.settings as settings_mod
    importlib.reload(settings_mod)
    import runtime_engine.deps as deps_mod
    importlib.reload(deps_mod)

    kb_v1 = deps_mod.get_kb()
    n_doctypes_v1 = len(kb_v1.doc_types)

    # Bump mtime on _base.yaml (touch). Sleep first so the new mtime is
    # detectably later than the load-time mtime (some filesystems have
    # 1-second resolution).
    time.sleep(1.1)
    base = PACKS_DIR / "_base.yaml"
    new_time = time.time()
    os.utime(base, (new_time, new_time))

    kb_v2 = deps_mod.get_kb()
    # New instance — was rebuilt; content equivalent.
    assert kb_v2 is not kb_v1
    assert len(kb_v2.doc_types) == n_doctypes_v1


# ---------- webhook NotificationSink ----------

def test_webhook_notify_posts_json(tmp_path) -> None:
    """The webhook sink POSTs JSON to its URL. We mock the urlopen call."""
    from unittest.mock import patch

    from plugins.notification_sink.webhook import WebhookNotificationSink
    from shared.protocols.notification_sink import EventLevel

    sink = WebhookNotificationSink("http://example.invalid/hook", allow_http=True)

    captured: dict = {}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b""

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["method"] = req.method
        return FakeResp()

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        sink.notify(EventLevel.WARNING, "test message", context={"doc": "x.pdf"})

    assert captured["url"] == "http://example.invalid/hook"
    assert captured["method"] == "POST"
    import json
    payload = json.loads(captured["body"])
    assert payload["level"] == "warning"
    assert payload["message"] == "test message"
    assert payload["context"] == {"doc": "x.pdf"}
