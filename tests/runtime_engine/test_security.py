"""Security regressions: path traversal, input validation, error truncation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from plugins.document_destination import FilesystemDestination
from plugins.notification_sink.webhook import WebhookNotificationSink
from shared.protocols.document_destination import DestinationError


# ---------- filesystem destination ----------

def test_filesystem_rejects_dotdot_segment(tmp_path) -> None:
    """`..` in a destination path must be rejected before placement."""
    dest = FilesystemDestination(root=tmp_path / "root", auto_create=True)
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(DestinationError):
        dest.place(src, "../escape/x.pdf")


def test_filesystem_rejects_absolute_segment(tmp_path) -> None:
    """A leading-slash segment must be rejected."""
    dest = FilesystemDestination(root=tmp_path / "root", auto_create=True)
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(DestinationError):
        dest.place(src, "/etc/passwd")


def test_filesystem_rejects_drive_prefix_segment(tmp_path) -> None:
    """Windows-style drive prefix segments must be rejected."""
    dest = FilesystemDestination(root=tmp_path / "root", auto_create=True)
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF")
    with pytest.raises(DestinationError):
        dest.place(src, "C:Windows/System32/x.pdf")


def test_filesystem_accepts_normal_path(tmp_path) -> None:
    dest = FilesystemDestination(root=tmp_path / "root", auto_create=True)
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF")
    final = dest.place(src, "invoice/2026/normal.pdf")
    assert Path(final).exists()
    assert Path(final).is_relative_to(tmp_path / "root")


# ---------- webhook https-only ----------

def test_webhook_rejects_http_by_default() -> None:
    with pytest.raises(ValueError):
        WebhookNotificationSink("http://example.invalid/hook")


def test_webhook_accepts_https() -> None:
    sink = WebhookNotificationSink("https://example.invalid/hook")
    assert sink.name == "webhook"


def test_webhook_allows_http_with_explicit_opt_in() -> None:
    sink = WebhookNotificationSink("http://example.invalid/hook", allow_http=True)
    assert sink.name == "webhook"


# ---------- auth: /setup transition ----------

def test_setup_open_during_first_run_then_authed() -> None:
    """`/setup` is open until is_wizard_complete() returns True; then it requires auth."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime_engine.auth import BearerTokenMiddleware

    wizard_complete = False

    def is_complete() -> bool:
        return wizard_complete

    app = FastAPI()

    @app.get("/setup")
    def setup() -> dict:
        return {"ok": True}

    @app.get("/healthz")
    def hz() -> dict:
        return {"ok": True}

    app.add_middleware(
        BearerTokenMiddleware,
        expected_token="sec",
        is_wizard_complete=is_complete,
    )
    client = TestClient(app)

    # First-run: /setup is open.
    assert client.get("/setup").status_code == 200

    # Once the wizard is complete, /setup requires the token like every
    # other state-changing endpoint.
    wizard_complete = True
    assert client.get("/setup").status_code == 401
    assert client.get("/setup", headers={"Authorization": "Bearer sec"}).status_code == 200

    # /healthz stays open in both states.
    assert client.get("/healthz").status_code == 200
