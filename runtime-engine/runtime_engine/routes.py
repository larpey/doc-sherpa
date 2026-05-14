"""HTTP routes for the runtime engine. Wiring only — logic in sibling modules."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from . import log as log_module
from . import templates
from .deps import get_destination, get_kb, get_routing_config
from .settings import settings

router = APIRouter()


@router.get("/healthz", response_class=Response)
def healthz() -> Response:
    return Response(content="ok", media_type="text/plain")


@router.get("/", response_class=HTMLResponse)
def inbox() -> HTMLResponse:
    """The single-page inbox. Lists recent classifications + per-row actions."""
    entries = log_module.list_recent(settings.db_path, limit=50)
    doc_types = sorted(get_kb().doc_types.keys())
    return HTMLResponse(templates.render_inbox(entries, doc_types))


@router.post("/classifications/{entry_id}/accept")
def accept(entry_id: str) -> RedirectResponse:
    """Mark a classification as accepted. No file move."""
    entry = log_module.get(settings.db_path, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown id {entry_id}")
    log_module.mark_accepted(settings.db_path, entry_id)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classifications/{entry_id}/correct")
def correct(
    entry_id: str,
    doc_type: str = Form(...),
    vendor: str = Form(""),
) -> RedirectResponse:
    """Apply a correction: new doc_type and/or vendor → move the file, log it."""
    entry = log_module.get(settings.db_path, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown id {entry_id}")

    known = set(get_kb().doc_types.keys())
    if doc_type not in known:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unknown doc_type {doc_type!r}. "
                f"Must be one of: {', '.join(sorted(known))}"
            ),
        )

    log_module.apply_correction(
        settings.db_path,
        entry_id,
        new_doc_type=doc_type,
        new_vendor=vendor.strip() or None,
        routing_config=get_routing_config(),
        destination=get_destination(),
    )
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
