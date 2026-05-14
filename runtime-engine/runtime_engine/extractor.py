"""Compile extractor specs from the KB into runtime extractors.

A spec like:
    kind: labeled_value
    labels: ["PO #", "Purchase Order #"]
    shape: { kind: alphanumeric, min_length: 4, max_length: 20 }

becomes a callable `(ocr_text) -> str | None`. The spec is the artifact
the system reads/writes; the regex is an implementation detail compiled
on demand. No human ever hand-writes the regex.

Vendor-level specs override doc-type-level specs for the same field.
That's how "Heineken BOL number lives after 'PO #'" becomes a vendor-
specific extraction without polluting the generic BOL spec.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from shared.types import (
    DocTypeDefinition,
    DocTypeName,
    ExtractorSpec,
    FieldName,
    KnowledgeBase,
    ValueShape,
    VendorName,
)

# Character classes per `kind`. Kept narrow on purpose — over-broad
# classes turn into "match anything" extractors that drift over time.
_SHAPE_CHAR_CLASS = {
    "alphanumeric": r"[A-Za-z0-9][A-Za-z0-9\-_/]*",
    "digits": r"\d+",
    "currency": r"\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?",
    "date": r"[A-Za-z0-9,/\- ]{4,30}",
}


def _shape_to_regex(shape: ValueShape) -> str:
    """Build the regex fragment that the captured value must satisfy."""
    base = _SHAPE_CHAR_CLASS.get(shape.kind, _SHAPE_CHAR_CLASS["alphanumeric"])
    # min/max length is enforced post-capture; baking it into the regex
    # makes the alternation regex unreadable and not much faster.
    return base


def compile_labeled_value(spec: ExtractorSpec) -> Callable[[str], str | None]:
    """Compile a labeled_value spec into a function over OCR text.

    The compiled regex tries each label in order, captures the immediately-
    following value matching the shape, and returns the first hit that
    also satisfies the length constraints.
    """
    if not spec.labels:
        return lambda _text: None

    shape = spec.shape
    value_pat = _shape_to_regex(shape)
    label_pat = "|".join(re.escape(label) for label in spec.labels)
    # Allow optional `#`, `No.`, `:` between the label and the value, plus
    # whitespace. The shape regex handles the value's characters.
    full = re.compile(
        rf"(?:{label_pat})\s*[#:.]*\s*({value_pat})",
        re.IGNORECASE,
    )

    def _extract(text: str) -> str | None:
        for match in full.finditer(text):
            value = match.group(1).strip().strip(".,;:")
            if shape.uppercase:
                value = value.upper()
            if shape.min_length <= len(value) <= shape.max_length:
                return value
        return None

    return _extract


_COMPILERS: dict[str, Callable[[ExtractorSpec], Callable[[str], str | None]]] = {
    "labeled_value": compile_labeled_value,
}


def compile_spec(spec: ExtractorSpec) -> Callable[[str], str | None]:
    """Dispatch to the right compiler for `spec.kind`.

    Unknown kinds return a "never matches" extractor rather than raising —
    a typo in a pack should not bring down classification for a whole
    install. The kind appears in logs from the calling site.
    """
    compiler = _COMPILERS.get(spec.kind)
    if compiler is None:
        return lambda _text: None
    return compiler(spec)


def extract_fields(
    ocr_text: str,
    kb: KnowledgeBase,
    doc_type: DocTypeName,
    vendor: VendorName | None,
) -> dict[FieldName, str]:
    """Run all field extractors for `(doc_type, vendor)` against `ocr_text`.

    Vendor-level extractors override doc-type-level for the same field.
    Returns only fields where extraction succeeded — missing fields stay
    missing rather than appearing with empty values.
    """
    dt: DocTypeDefinition | None = kb.doc_types.get(doc_type)
    if dt is None:
        return {}

    vendor_overrides: dict[str, ExtractorSpec] = {}
    if vendor is not None and vendor in kb.vendors:
        vendor_overrides = kb.vendors[vendor].field_extractors

    result: dict[FieldName, str] = {}
    for field_def in dt.fields:
        key = f"{doc_type}.{field_def.name}"
        spec = vendor_overrides.get(key, field_def.extractor)
        value = compile_spec(spec)(ocr_text)
        if value is not None:
            result[field_def.name] = value
    return result
