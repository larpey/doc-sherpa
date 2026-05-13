"""Service-level test for the upload → label flow.

One test per major flow; this is *the* flow for phase 1. Uses FastAPI's
TestClient (httpx under the hood) against a fresh app whose storage is
redirected to a tmpdir, so the test never touches the developer's real
storage dir.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Build a TestClient against an app whose storage points at tmp_path.

    `pydantic-settings` reads env vars at import time of the settings
    module, so we set them *before* importing anything from
    `training_service`, then `importlib.reload` to make sure caches are
    fresh between tests.
    """
    monkeypatch.setenv("TRAINING_SERVICE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("TRAINING_SERVICE_DATABASE_FILENAME", "training.db")

    # Reload settings + downstream modules so the env-var override sticks.
    import training_service.settings as settings_mod
    importlib.reload(settings_mod)
    import training_service.db as db_mod
    importlib.reload(db_mod)
    import training_service.sessions as sessions_mod
    importlib.reload(sessions_mod)
    import training_service.documents as documents_mod
    importlib.reload(documents_mod)
    import training_service.labels as labels_mod
    importlib.reload(labels_mod)
    import training_service.templates as templates_mod
    importlib.reload(templates_mod)
    import training_service.routes as routes_mod
    importlib.reload(routes_mod)
    import training_service.main as main_mod
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as c:
        yield c


def _fake_pdf(filename: str = "sample.pdf") -> tuple[str, bytes, str]:
    """Return a tuple matching httpx's files= shape: (name, content, mime).

    The content isn't a valid PDF byte-for-byte but it's enough to exercise
    the storage path; OCR isn't part of phase 1 deliverable 3.
    """
    return (filename, b"%PDF-1.4\nfake content for testing\n%%EOF\n", "application/pdf")


def test_healthz(client: TestClient) -> None:
    """The liveness probe answers `ok`."""
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.text == "ok"


def test_full_upload_and_label_flow(client: TestClient) -> None:
    """End-to-end: create session, upload two PDFs, label both, see done page."""
    # Create session — expect a redirect to the upload page.
    r = client.post("/sessions", follow_redirects=False)
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith("/sessions/")
    assert location.endswith("/upload")
    session_id = location.split("/")[2]

    # Upload two fake PDFs in one request.
    files = [("files", _fake_pdf("invoice_A.pdf")), ("files", _fake_pdf("bol_B.pdf"))]
    r = client.post(f"/sessions/{session_id}/documents", files=files, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"] == f"/sessions/{session_id}/label"

    # Fetch the labeling page; both filenames should be present.
    r = client.get(f"/sessions/{session_id}/label")
    assert r.status_code == 200
    body = r.text
    assert "invoice_A.pdf" in body
    assert "bol_B.pdf" in body
    assert "doc_type__" in body  # form fields keyed by document id

    # The labeling form puts document IDs in input names; scrape them out
    # rather than introducing an extra DB-peek dependency in the test.
    import re

    doc_ids = sorted(set(re.findall(r"doc_type__([a-f0-9]{32})", body)))
    assert len(doc_ids) == 2, f"expected 2 documents, got {doc_ids}"

    # Submit labels for both.
    form_data = {}
    for doc_id, doc_type in zip(doc_ids, ["invoice", "bol"]):
        form_data[f"doc_type__{doc_id}"] = doc_type
        form_data[f"vendor__{doc_id}"] = "TestVendor"
        form_data[f"identifier__{doc_id}"] = f"NUM-{doc_id[:6]}"
        form_data[f"customer__{doc_id}"] = "Test Customer"

    r = client.post(f"/sessions/{session_id}/labels", data=form_data, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert r.headers["location"].startswith(f"/sessions/{session_id}/done")

    # Done page renders the saved-count.
    r = client.get(r.headers["location"])
    assert r.status_code == 200
    assert "Labels saved" in r.text
    assert "2 label" in r.text

    # Round-trip: re-open labeling page, vendor pre-fills from saved labels.
    r = client.get(f"/sessions/{session_id}/label")
    assert r.status_code == 200
    assert r.text.count("TestVendor") == 2


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    """Non-PDF uploads return 400 with an operator-readable message."""
    r = client.post("/sessions", follow_redirects=False)
    session_id = r.headers["location"].split("/")[2]
    files = [("files", ("note.txt", b"hello", "text/plain"))]
    r = client.post(f"/sessions/{session_id}/documents", files=files, follow_redirects=False)
    assert r.status_code == 400
    assert "pdf" in r.json()["detail"].lower()


def test_label_page_for_empty_session(client: TestClient) -> None:
    """Visiting /label on a session with no docs shows the empty hint, not 500."""
    r = client.post("/sessions", follow_redirects=False)
    session_id = r.headers["location"].split("/")[2]
    r = client.get(f"/sessions/{session_id}/label")
    assert r.status_code == 200
    assert "No documents uploaded yet" in r.text


def test_unknown_session_returns_404(client: TestClient) -> None:
    """Random session ID → 404 with a helpful detail."""
    r = client.get("/sessions/deadbeef" * 4 + "/label")
    assert r.status_code == 404
