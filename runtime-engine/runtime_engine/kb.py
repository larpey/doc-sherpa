"""Load and merge knowledge-base packs into an in-memory KnowledgeBase.

Packs are YAML files in `packs/`. `_base.yaml` is always loaded first;
industry packs (`logistics.yaml`, `healthcare.yaml`, …) merge on top.
Later, customer-local learnings from a SQLite overlay merge on top of
that — same shape, different source.

Merging rules:
- Doc types: later pack's entry replaces the earlier one entirely. We do
  not yet support partial-merge (adding keywords to an existing type
  without redefining it) — that's a learned-overlay concern, not a pack
  concern.
- Vendors: each vendor is a single canonical entry; duplicates are an
  error (catches accidentally redefining "Heineken" in two packs).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from shared.types import (
    DocTypeDefinition,
    ExtractorSpec,
    FieldDefinition,
    KeywordHint,
    KnowledgeBase,
    ValueShape,
    VendorDefinition,
)


class PackLoadError(Exception):
    """Raised when a pack file is malformed or duplicates a name."""


def _build_value_shape(raw: dict[str, Any] | None) -> ValueShape:
    if not raw:
        return ValueShape()
    return ValueShape(
        kind=raw.get("kind", "alphanumeric"),
        min_length=int(raw.get("min_length", 1)),
        max_length=int(raw.get("max_length", 60)),
        uppercase=bool(raw.get("uppercase", False)),
    )


def _build_extractor(raw: dict[str, Any]) -> ExtractorSpec:
    return ExtractorSpec(
        kind=raw["kind"],
        labels=tuple(raw.get("labels", ())),
        pattern=raw.get("pattern"),
        shape=_build_value_shape(raw.get("shape")),
        within_lines=int(raw.get("within_lines", 1)),
    )


def _build_doc_type(raw: dict[str, Any]) -> DocTypeDefinition:
    return DocTypeDefinition(
        name=raw["name"],
        description=raw.get("description", ""),
        industries=tuple(raw.get("industries", ())),
        keywords=tuple(
            KeywordHint(phrase=k["phrase"], weight=float(k.get("weight", 0.5)))
            for k in raw.get("keywords", [])
        ),
        fields=tuple(
            FieldDefinition(
                name=f["name"],
                required=bool(f.get("required", False)),
                extractor=_build_extractor(f["extractor"]),
            )
            for f in raw.get("fields", [])
        ),
    )


def _build_vendor(raw: dict[str, Any]) -> VendorDefinition:
    return VendorDefinition(
        canonical=raw["canonical"],
        aliases=tuple(raw.get("aliases", ())),
        industries=tuple(raw.get("industries", ())),
        likely_doc_types=tuple(raw.get("likely_doc_types", ())),
        field_extractors={
            key: _build_extractor(spec)
            for key, spec in (raw.get("field_extractors") or {}).items()
        },
    )


def load_pack(path: Path) -> tuple[list[DocTypeDefinition], list[VendorDefinition], dict[str, Any]]:
    """Read one YAML pack from disk. Returns (doc_types, vendors, metadata)."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackLoadError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PackLoadError(f"Pack {path} must be a mapping at the top level")

    metadata = raw.get("metadata", {})
    doc_types = [_build_doc_type(dt) for dt in raw.get("doc_types", [])]
    vendors = [_build_vendor(v) for v in raw.get("vendors", [])]
    return doc_types, vendors, metadata


def merge_packs(kb: KnowledgeBase, *paths: Path) -> KnowledgeBase:
    """Load packs in order and merge into `kb` in place. Returns the same `kb`."""
    for path in paths:
        doc_types, vendors, metadata = load_pack(path)
        for dt in doc_types:
            kb.doc_types[dt.name] = dt  # later wins
        for v in vendors:
            if v.canonical in kb.vendors:
                raise PackLoadError(
                    f"Duplicate vendor {v.canonical!r}: already defined "
                    f"in {kb.sources}, redefined in {path}"
                )
            kb.vendors[v.canonical] = v
        name = metadata.get("name") or path.stem
        kb.sources.append(name)
    return kb


def load_kb(packs_dir: Path, include: list[str] | None = None) -> KnowledgeBase:
    """Load the base pack + named industry packs from `packs_dir`.

    `_base.yaml` is always loaded first. `include` is a list of stems
    (without `.yaml`) — defaults to `[]`, i.e. base only. Pass
    `["logistics"]` to add logistics, etc.
    """
    base_path = packs_dir / "_base.yaml"
    if not base_path.exists():
        raise PackLoadError(f"Base pack not found at {base_path}")

    paths: list[Path] = [base_path]
    for name in include or []:
        p = packs_dir / f"{name}.yaml"
        if not p.exists():
            raise PackLoadError(f"Pack not found: {p}")
        paths.append(p)

    return merge_packs(KnowledgeBase(), *paths)
