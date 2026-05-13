"""HTTP routes for the training service. Wiring only — logic lives elsewhere.

Each handler is small on purpose: parse the request, call into a CRUD
module, render or redirect. If a handler grows past ~30 lines it's almost
always because logic crept in; push it back into `sessions.py`,
`documents.py`, or `labels.py`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from shared.types import DocumentType

from . import documents as documents_module
from . import labels as labels_module
from . import sessions as sessions_module
from . import templates
from .settings import settings

router = APIRouter()


def _ensure_session(session_id: str) -> sessions_module.Session:
    """Look up a session or 404. Centralized so all handlers behave the same."""
    session = sessions_module.get_session(settings.database_path, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"session {session_id!r} not found")
    return session


@router.get("/healthz", response_class=Response)
def healthz() -> Response:
    """Liveness probe. Returns plain `ok` so curl-checking is trivial."""
    return Response(content="ok", media_type="text/plain")


@router.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    """Landing page: list recent sessions + button to start a new one."""
    recent = sessions_module.list_sessions(settings.database_path, limit=10)
    return HTMLResponse(templates.render_home(recent))


@router.post("/sessions")
def create_session_route() -> RedirectResponse:
    """Create a new session and redirect to its upload page.

    303 See Other ensures the browser switches to GET on the redirect, the
    standard POST-redirect-GET pattern.
    """
    session = sessions_module.create_session(settings.database_path)
    return RedirectResponse(
        url=f"/sessions/{session.id}/upload",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/sessions/{session_id}/upload", response_class=HTMLResponse)
def session_upload_page(session_id: str) -> HTMLResponse:
    session = _ensure_session(session_id)
    return HTMLResponse(templates.render_session_upload(session))


@router.post("/sessions/{session_id}/documents")
async def upload_documents(
    session_id: str,
    files: list[UploadFile] = File(default=[]),
) -> RedirectResponse:
    """Accept multipart/form-data with one or more PDFs in the `files` field.

    The empty-default + manual length check gives us a 400 with an
    operator-readable message instead of FastAPI's default 422 when the
    field is missing entirely.
    """
    session = _ensure_session(session_id)

    if not files:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No files uploaded. The form field must be named 'files'.",
        )

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    existing = documents_module.count_documents(settings.database_path, session.id)
    remaining = settings.max_documents_per_session - existing
    if len(files) > remaining:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=(
                f"This upload would exceed the per-session cap "
                f"({settings.max_documents_per_session}). "
                f"{existing} already uploaded, {remaining} slot(s) remaining."
            ),
        )

    for upload in files:
        try:
            documents_module.save_document(
                db_path=settings.database_path,
                storage_dir=settings.storage_dir,
                session_id=session.id,
                original_filename=upload.filename or "unnamed.pdf",
                content_type=upload.content_type,
                source=upload.file,
                max_size_bytes=max_bytes,
            )
        except documents_module.UploadRejected as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return RedirectResponse(
        url=f"/sessions/{session.id}/label",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/sessions/{session_id}/label", response_class=HTMLResponse)
def session_label_page(session_id: str) -> HTMLResponse:
    session = _ensure_session(session_id)
    pairs = labels_module.list_documents_with_labels(settings.database_path, session.id)
    return HTMLResponse(templates.render_session_label(session, pairs))


@router.post("/sessions/{session_id}/labels")
async def save_labels(session_id: str, request: Request) -> RedirectResponse:
    """Save all labels for a session.

    Form field naming is `<field>__<document_id>`, so we iterate documents
    in the session and pluck their values out of the form. Documents not
    in the form are simply skipped — partial saves are not first-class but
    fall out naturally.
    """
    session = _ensure_session(session_id)
    form = await request.form()
    docs = documents_module.list_documents(settings.database_path, session.id)

    saved = 0
    for doc in docs:
        doc_type_raw = form.get(f"doc_type__{doc.id}")
        if not isinstance(doc_type_raw, str) or not doc_type_raw:
            continue
        try:
            doc_type = DocumentType(doc_type_raw)
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown doc_type {doc_type_raw!r} for document {doc.id}",
            ) from exc

        def _str_or_none(key: str) -> str | None:
            v = form.get(key)
            return v if isinstance(v, str) else None

        labels_module.upsert_label(
            db_path=settings.database_path,
            document_id=doc.id,
            doc_type=doc_type,
            vendor=_str_or_none(f"vendor__{doc.id}"),
            identifier=_str_or_none(f"identifier__{doc.id}"),
            customer=_str_or_none(f"customer__{doc.id}"),
        )
        saved += 1

    return RedirectResponse(
        url=f"/sessions/{session.id}/done?count={saved}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/sessions/{session_id}/done", response_class=HTMLResponse)
def session_done_page(session_id: str, count: int = 0) -> HTMLResponse:
    session = _ensure_session(session_id)
    return HTMLResponse(
        templates.render_session_done(session, count, datetime.now(timezone.utc))
    )
