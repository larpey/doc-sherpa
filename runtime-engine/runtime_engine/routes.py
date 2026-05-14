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


@router.get("/", response_class=HTMLResponse, response_model=None)
def inbox() -> RedirectResponse | HTMLResponse:
    """The single-page inbox. Lists recent classifications + per-row actions.

    First-run: if the wizard hasn't been completed, redirect to /setup.
    """
    if not wizard_module.is_complete(settings.db_path.parent):
        return RedirectResponse(url="/setup", status_code=status.HTTP_303_SEE_OTHER)
    entries = log_module.list_recent(settings.db_path, limit=50)
    doc_types = sorted(get_kb().doc_types.keys())
    folder_tree = _scan_destination_tree()
    return HTMLResponse(templates.render_inbox(entries, doc_types, folder_tree))


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


@router.post("/classifications/{entry_id}/route-to")
async def route_to(entry_id: str, request: Request) -> JSONResponse:
    """Drag-to-folder endpoint. Body: {"folder_rel": "bol/2026"}.

    The first path segment is taken as the corrected doc type; any
    vendor in the original classification is preserved. Returns JSON so
    the front-end can update the row in place without a full reload.
    """
    payload = await request.json()
    folder_rel = (payload.get("folder_rel") or "").strip().strip("/")
    if not folder_rel:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="folder_rel required")

    entry = log_module.get(settings.db_path, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"unknown id {entry_id}")

    # First path segment = the corrected doc type. If the operator drops
    # onto a never-seen-before folder, the doc type is created via the
    # learning overlay (handled inside apply_correction).
    new_doc_type = folder_rel.split("/", 1)[0]

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
