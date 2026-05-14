"""Lazy singletons for the KB, RoutingConfig, and destination plugin.

Loaded on first request, cached for the process. Tests reload this
module (along with settings) to reset between runs.

This is dependency-wiring code, not domain logic. Keep it boring.
"""

from __future__ import annotations

from pathlib import Path

from plugins.document_destination import FilesystemDestination
from runtime_engine.kb import load_kb
from shared.types import KnowledgeBase

from .router import RoutingConfig
from .settings import settings

_kb: KnowledgeBase | None = None
_routing_config: RoutingConfig | None = None
_destination: FilesystemDestination | None = None


def _packs_dir() -> Path:
    if settings.packs_dir is not None:
        return settings.packs_dir
    return Path(__file__).resolve().parents[2] / "packs"


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = load_kb(_packs_dir(), include=list(settings.active_packs))
    return _kb


def get_routing_config() -> RoutingConfig:
    global _routing_config
    if _routing_config is None:
        _routing_config = RoutingConfig(
            template=settings.routing_template,
            unclassified_template=settings.unclassified_template,
            confidence_threshold=settings.confidence_threshold,
        )
    return _routing_config


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
    global _kb, _routing_config, _destination
    _kb = None
    _routing_config = None
    _destination = None
