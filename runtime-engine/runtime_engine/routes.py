"""HTTP routes for the runtime engine. Wiring only — logic in sibling modules."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import log as log_module
from . import templates
from . import wizard as wizard_module
from . import wizard_templates
from .deps import (
    get_destination,
    get_kb,
    get_routing_config,
    invalidate_all,
    invalidate_kb_cache,
)
from .pipeline import process_document
from .hashing import file_sha256
from .settings import settings

router = APIRouter()


def _packs_dir() -> Path:
    if settings.packs_dir is not None:
        return settings.packs_dir
    return Path(__file__).resolve().parents[2] / "packs"


def _available_packs() -> list[str]:
    """Names of every pack under `packs/`, excluding the `_base` pack."""
    return sorted(
        p.stem for p in _packs_dir().glob("*.yaml") if not p.stem.startswith("_")
    )


@router.get("/healthz", response_class=Response)
def healthz() -> Response:
    return Response(content="ok", media_type="text/plain")


_VALID_STATUSES: frozenset[str] = frozenset({"pending", "accepted", "corrected", "error"})


@router.get("/", response_class=HTMLResponse, response_model=None)
def inbox(
    page: int = 1,
    per_page: int = 50,
    status_filter: str | None = None,
) -> RedirectResponse | HTMLResponse:
    """The single-page inbox with pagination + status filter.

    Query: `?page=N&per_page=M&status_filter=pending|accepted|corrected|error`.
    First-run: if the wizard hasn't been completed, redirect to /setup.
    """
    if not wizard_module.is_complete(settings.db_path.parent):
        return RedirectResponse(url="/setup", status_code=status.HTTP_303_SEE_OTHER)
    if status_filter is not None and status_filter not in _VALID_STATUSES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"status_filter must be one of {sorted(_VALID_STATUSES)}",
        )
    page = max(1, page)
    per_page = max(1, min(200, per_page))
    offset = (page - 1) * per_page
    entries = log_module.list_recent(
        settings.db_path,
        limit=per_page,
        offset=offset,
        status=status_filter,
    )
    doc_types = sorted(get_kb().doc_types.keys())
    folder_tree = _scan_destination_tree()
    return HTMLResponse(
        templates.render_inbox(
            entries,
            doc_types,
            folder_tree,
            page=page,
            per_page=per_page,
            status_filter=status_filter,
        )
    )


def _scan_destination_tree() -> list[dict]:
    """Walk the destination root and return a shallow tree of existing folders.

    Used by the drag-to-folder UI as drop targets. We only descend one
    or two levels — deep folder trees are noisy and the operator usually
    wants a top-level destination anyway.
    """
    root = settings.destination_root
    if not root.exists():
        return []
    tree: list[dict] = []
    try:
        for first in sorted(p for p in root.iterdir() if p.is_dir()):
            children = []
            try:
                for second in sorted(p for p in first.iterdir() if p.is_dir()):
                    children.append({"name": second.name, "rel": f"{first.name}/{second.name}"})
            except OSError:
                pass
            tree.append({"name": first.name, "rel": first.name, "children": children})
    except OSError:
        return []
    return tree


# ---------- setup wizard ----------

@router.get("/setup", response_class=HTMLResponse)
def setup_form() -> HTMLResponse:
    current = wizard_module.load(settings.db_path.parent)
    return HTMLResponse(
        wizard_templates.render_wizard(
            available_packs=_available_packs(),
            current=current,
        )
    )


@router.post("/setup", response_class=HTMLResponse, response_model=None)
async def setup_submit(request: Request) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    watch_dir = (form.get("watch_dir") or "").strip()
    dest_root = (form.get("destination_root") or "").strip()
    if not watch_dir or not dest_root:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Both watch folder and destination root are required.",
        )
    packs = tuple(p for p in form.getlist("packs") if isinstance(p, str))
    auto_create = form.get("auto_create_folders") == "1"

    cfg = wizard_module.WizardConfig(
        watch_dir=Path(watch_dir).expanduser(),
        destination_root=Path(dest_root).expanduser(),
        active_packs=packs,
        auto_create_folders=auto_create,
    )
    problems = wizard_module.validate_paths(cfg)
    if problems:
        return HTMLResponse(
            wizard_templates.render_wizard(
                available_packs=_available_packs(),
                current=cfg,
                problems=problems,
            )
        )

    wizard_module.save(settings.db_path.parent, cfg)

    # Update the running settings so the next request uses the new config.
    settings.watch_dir = cfg.watch_dir
    settings.destination_root = cfg.destination_root
    settings.active_packs = cfg.active_packs
    settings.auto_create_folders = cfg.auto_create_folders
    invalidate_all()

    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


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
    # Correction may have added overlay keywords / vendors — invalidate
    # the cached KB so the next classification picks them up.
    invalidate_kb_cache()
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classifications/{entry_id}/delete")
def delete_classification(entry_id: str) -> RedirectResponse:
    """Remove a row from the log; do not touch the placed file."""
    log_module.delete_entry(settings.db_path, entry_id, also_remove_file=False)
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classifications/{entry_id}/reprocess")
def reprocess_classification(entry_id: str) -> RedirectResponse:
    """Re-run the pipeline on a row's source file (typically used after errors).

    The original row is deleted first (so dedup doesn't refuse the
    reprocess), then the source file is re-fed to the pipeline.
    """
    entry = log_module.get(settings.db_path, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown id {entry_id}")
    source = Path(entry.source_path)
    if not source.exists():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="source file no longer exists; cannot reprocess",
        )
    log_module.delete_entry(settings.db_path, entry_id, also_remove_file=False)
    try:
        content_hash = file_sha256(source)
    except OSError:
        content_hash = None
    result = process_document(source, get_kb(), get_routing_config(), get_destination())
    log_module.record_result(
        settings.db_path,
        result,
        content_sha256=content_hash,
        auto_confirm_threshold=settings.auto_confirm_threshold,
    )
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/classifications/bulk-accept")
async def bulk_accept(request: Request) -> JSONResponse:
    """Accept multiple pending rows in one call. Body: {"ids":[...]}."""
    payload = await request.json()
    ids = payload.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="ids must be a list")
    accepted = 0
    for entry_id in ids:
        if not isinstance(entry_id, str):
            continue
        if log_module.get(settings.db_path, entry_id):
            log_module.mark_accepted(settings.db_path, entry_id)
            accepted += 1
    return JSONResponse({"accepted": accepted})


@router.get("/stats", response_class=JSONResponse)
def stats() -> JSONResponse:
    """Aggregate metrics: counts per status + average confidence.

    Drives the dashboard widgets and is the right shape for a /metrics
    Prometheus exporter later.
    """
    counts = log_module.status_counts(settings.db_path)
    return JSONResponse(
        {
            "counts": counts,
            "total": sum(counts.values()),
            "average_confidence": round(log_module.average_confidence(settings.db_path), 3),
        }
    )


@router.post("/admin/reload-kb")
def reload_kb() -> JSONResponse:
    """Force a KB rebuild — useful after editing pack YAMLs in place."""
    invalidate_kb_cache()
    kb = get_kb()
    return JSONResponse(
        {
            "doc_types": len(kb.doc_types),
            "vendors": len(kb.vendors),
            "sources": kb.sources,
        }
    )


@router.post("/classifications/{entry_id}/route-to")
async def route_to(entry_id: str, request: Request) -> JSONResponse:
    """Drag-to-folder endpoint. Body: {"folder_rel": "bol/2026"}.

    The first path segment is taken as the corrected doc type; any
    vendor in the original classification is preserved. Returns JSON so
    the front-end can update the row in place without a full reload.
    """
    payload = await request.json()
    folder_rel = (payload.get("folder_rel") or "").strip().strip("/").strip("\\")
    if not folder_rel:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="folder_rel required")

    # Reject path traversal — `..` segments, absolute roots, drive prefixes.
    segments = [s for s in folder_rel.replace("\\", "/").split("/") if s]
    for segment in segments:
        if segment in {"..", "."} or ":" in segment:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"unsafe folder_rel segment: {segment!r}",
            )

    entry = log_module.get(settings.db_path, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown id {entry_id}")

    # First path segment = the corrected doc type. If the operator drops
    # onto a never-seen-before folder, the doc type is created via the
    # learning overlay (handled inside apply_correction).
    new_doc_type = segments[0]

    log_module.apply_correction(
        settings.db_path,
        entry_id,
        new_doc_type=new_doc_type,
        new_vendor=entry.vendor,  # preserve whatever was classified
        routing_config=get_routing_config(),
        destination=get_destination(),
    )
    invalidate_kb_cache()
    updated = log_module.get(settings.db_path, entry_id)
    return JSONResponse(
        {
            "id": entry_id,
            "status": updated.status if updated else "unknown",
            "final_path": updated.final_path if updated else None,
        }
    )
