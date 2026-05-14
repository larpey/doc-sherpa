"""Cached access to the active KnowledgeBase for the training service.

The training service uses the KB only for the doc-type vocabulary (it
populates the labeling dropdown). The runtime engine is the heavy user.

We cache the loaded KB in a module-level variable rather than wiring
dependency-injection through every route — for a single-tenant POC this
is simpler and just as testable (tests reload the module to reset).
"""

from __future__ import annotations

from pathlib import Path

from runtime_engine.kb import load_kb
from shared.types import KnowledgeBase

from .settings import settings

_kb: KnowledgeBase | None = None


def _packs_dir() -> Path:
    """Resolve the packs directory.

    Settings allow override; default is `<project>/packs`. Project root
    is two levels up from this file: training-service/training_service/.
    """
    if settings.packs_dir is not None:
        return settings.packs_dir
    return Path(__file__).resolve().parents[2] / "packs"


def get_kb() -> KnowledgeBase:
    """Return the active KB, loading on first call. Process-cached after."""
    global _kb
    if _kb is None:
        _kb = load_kb(_packs_dir(), include=list(settings.active_packs))
    return _kb


def reset_kb_cache() -> None:
    """Drop the cache (used in tests when settings change)."""
    global _kb
    _kb = None
