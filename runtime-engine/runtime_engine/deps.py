"""Lazy singletons for the KB, RoutingConfig, and destination plugin.

Loaded on first request, cached for the process. Tests reload this
module (along with settings) to reset between runs.

This is dependency-wiring code, not domain logic. Keep it boring.
"""

from __future__ import annotations

from pathlib import Path

from plugins.ai_classifier import ClaudeClassifier
from plugins.ai_classifier.claude import is_available as llm_available
from plugins.document_destination import FilesystemDestination
from runtime_engine.kb import load_kb
from shared.types import KnowledgeBase

from . import overlay as overlay_module
from .router import RoutingConfig
from .settings import settings

_kb: KnowledgeBase | None = None
_routing_config: RoutingConfig | None = None
_destination: FilesystemDestination | None = None
_ai_fallback: ClaudeClassifier | None = None


def _packs_dir() -> Path:
    if settings.packs_dir is not None:
        return settings.packs_dir
    return Path(__file__).resolve().parents[2] / "packs"


_packs_mtime_at_load: float = 0.0


def _packs_mtime_max() -> float:
    """Latest mtime across the active packs + base. 0 if dir missing."""
    pdir = _packs_dir()
    if not pdir.exists():
        return 0.0
    candidates = [pdir / "_base.yaml"] + [pdir / f"{p}.yaml" for p in settings.active_packs]
    latest = 0.0
    for c in candidates:
        try:
            latest = max(latest, c.stat().st_mtime)
        except OSError:
            continue
    return latest


def get_kb() -> KnowledgeBase:
    """Return pack KB merged with the local overlay.

    Cached for the process; invalidated automatically when any active
    pack YAML's mtime changes (operator edited a pack), or manually via
    `invalidate_kb_cache()` after the overlay receives new rows.
    """
    global _kb, _packs_mtime_at_load
    current_mtime = _packs_mtime_max()
    if _kb is not None and current_mtime > _packs_mtime_at_load:
        # Operator edited a pack file — reload from scratch.
        _kb = None

    if _kb is None:
        _kb = load_kb(_packs_dir(), include=list(settings.active_packs))
        overlay_module.init_overlay(settings.db_path)
        overlay_module.merge_overlay_into(_kb, settings.db_path)
        _packs_mtime_at_load = current_mtime
    return _kb


def invalidate_kb_cache() -> None:
    """Drop the cached KB so the next `get_kb()` rebuilds with fresh overlay."""
    global _kb
    _kb = None


def get_routing_config() -> RoutingConfig:
    global _routing_config
    if _routing_config is None:
        _routing_config = RoutingConfig(
            template=settings.routing_template,
            unclassified_template=settings.unclassified_template,
            confidence_threshold=settings.confidence_threshold,
        )
    return _routing_config


def get_ai_fallback() -> ClaudeClassifier | None:
    """Return the LLM fallback plugin, or None if not configured.

    A `None` return means the pipeline runs without an LLM safety net —
    fine for installs that don't want to send OCR text to Anthropic.
    """
    global _ai_fallback
    if not llm_available():
        return None
    if _ai_fallback is None:
        _ai_fallback = ClaudeClassifier()
    return _ai_fallback


def get_destination() -> FilesystemDestination:
    global _destination
    if _destination is None:
        _destination = FilesystemDestination(
            root=settings.destination_root,
            auto_create=settings.auto_create_folders,
        )
    return _destination


def reset_all() -> None:
    """Drop all cached singletons. Used in tests."""
    global _kb, _routing_config, _destination, _ai_fallback
    _kb = None
    _routing_config = None
    _destination = None
    _ai_fallback = None


def invalidate_all() -> None:
    """Alias of reset_all for the setup-wizard path. Same effect, clearer at the call site."""
    reset_all()
