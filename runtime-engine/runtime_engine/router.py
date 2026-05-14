"""Render a destination path for a classified document.

Pure function. Given:
- the Classification (doc type, vendor, identifier, etc.)
- the original file path (for fallback name + extension)
- a RoutingConfig (template + threshold + unclassified dir)

returns a POSIX-style relative path that the DocumentDestination plugin
writes to. The plugin owns the *transport*; this module owns the
*decision*.

Template vocabulary: `{doc_type}`, `{vendor}`, `{identifier}`,
`{customer}`, `{year}`, `{month}`, `{day}`, `{date}`, `{original_filename}`.
Missing values fall back to `"unknown"` so the document still gets placed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from shared.types import Classification

# Characters we strip from filename components. Permissive enough to keep
# vendor names readable (no over-aggressive replacement); strict enough
# to avoid breaking Windows / SMB / Drive path rules.
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class RoutingConfig:
    """Routing knobs the operator sets in the first-run wizard.

    All paths are POSIX-style for portability; the filesystem plugin
    translates to the host's native separator at write time.
    """

    template: str = "{doc_type}/{year}/{vendor}_{identifier}.pdf"
    unclassified_template: str = "unclassified/{year}/{original_filename}"
    confidence_threshold: float = 0.30
    fallback_value: str = "unknown"
    overrides: tuple["RouteOverride", ...] = ()


@dataclass(frozen=True, slots=True)
class RouteOverride:
    """A per-(doc_type, vendor) routing override.

    First match wins. Either `doc_type` or `vendor` is required; both is
    a tighter match. `None` for a field means 'any.' Operators add
    overrides through the review-queue UI; the model never sees rule
    syntax in normal use.
    """

    template: str
    doc_type: str | None = None
    vendor: str | None = None


def _sanitize(component: str) -> str:
    """Strip path-illegal characters and collapse whitespace runs."""
    safe = _UNSAFE_FILENAME_CHARS.sub("", component)
    safe = re.sub(r"\s+", "_", safe.strip())
    return safe or "unknown"


def _build_vars(
    classification: Classification,
    original_path: Path,
    now: datetime,
    fallback: str,
) -> dict[str, str]:
    """Build the substitution dict from a Classification + clock."""
    invoice_or_bol = classification.fields.get("invoice_number") or classification.fields.get(
        "bol_number"
    ) or classification.fields.get("po_number")

    return {
        "doc_type": _sanitize(classification.doc_type or fallback),
        "vendor": _sanitize(classification.vendor or fallback),
        "identifier": _sanitize(invoice_or_bol or fallback),
        "customer": _sanitize(classification.fields.get("customer") or fallback),
        "year": now.strftime("%Y"),
        "month": now.strftime("%m"),
        "day": now.strftime("%d"),
        "date": now.strftime("%Y-%m-%d"),
        "original_filename": _sanitize(original_path.name),
    }


def _match_override(
    config: RoutingConfig, classification: Classification
) -> RouteOverride | None:
    """Return the first override that matches this classification, if any."""
    for ovr in config.overrides:
        if ovr.doc_type is not None and ovr.doc_type != classification.doc_type:
            continue
        if ovr.vendor is not None and ovr.vendor != classification.vendor:
            continue
        return ovr
    return None


def is_unclassified(classification: Classification, config: RoutingConfig) -> bool:
    """Whether this classification should go to the unclassified folder.

    Two conditions: no doc type assigned, OR confidence below the
    operator's threshold. Both are treated as 'don't trust me'.
    """
    if classification.doc_type is None:
        return True
    if classification.confidence < config.confidence_threshold:
        return True
    return False


def render_destination(
    classification: Classification,
    original_path: Path,
    config: RoutingConfig,
    now: datetime | None = None,
) -> str:
    """Return the relative destination path for this document.

    The path is relative to the destination plugin's root. Always uses
    forward slashes; the plugin handles host-native translation.
    """
    when = now or datetime.now(timezone.utc)
    variables = _build_vars(classification, original_path, when, config.fallback_value)

    if is_unclassified(classification, config):
        template = config.unclassified_template
    else:
        override = _match_override(config, classification)
        template = override.template if override else config.template

    try:
        return template.format(**variables)
    except KeyError as exc:
        # Unknown placeholder in a template. Don't crash the whole
        # pipeline; route to unclassified with a note so the operator
        # can fix the template via the wizard.
        return f"unclassified/template_error/{variables['original_filename']}"
