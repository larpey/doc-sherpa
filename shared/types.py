"""Shared types — Knowledge Base primitives + classification results.

The KB is the source of truth for what doc types exist, what their
distinctive signals are, who the known vendors are, and how to pull
structured fields out of OCR text. Both the runtime engine and the
training/setup tooling read from it.

Doc type and vendor names are **strings**, not enums. The vocabulary is a
property of the loaded KB, not the product. A clinic and a beverage
distributor have different doc types; both ship with the same code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Type aliases — clarify intent without buying a class hierarchy.
DocTypeName = str
VendorName = str
FieldName = str
IndustryName = str


# ---------- Extractor specs (how to pull a value out of OCR text) ----------

@dataclass(frozen=True, slots=True)
class ValueShape:
    """Constraints on what a captured value should look like.

    `kind` is one of: `alphanumeric`, `digits`, `currency`, `date`.
    Other constraints narrow further. Used by the extractor to reject
    OCR noise that happens to land near the right label.
    """

    kind: str = "alphanumeric"
    min_length: int = 1
    max_length: int = 60
    uppercase: bool = False


@dataclass(frozen=True, slots=True)
class ExtractorSpec:
    """How to find a specific field value in OCR text.

    Today supports the `labeled_value` kind: find one of the `labels`,
    capture the value that follows. Future kinds (`positional`, `regex`,
    `llm`) plug in here without changing callers.
    """

    kind: str  # "labeled_value" | "regex" | "llm" | ...
    labels: tuple[str, ...] = ()       # for labeled_value: keywords the value follows
    pattern: str | None = None          # for regex: a raw pattern (compiled lazily)
    shape: ValueShape = field(default_factory=ValueShape)
    within_lines: int = 1               # for labeled_value: lookahead window


# ---------- Knowledge base entries ----------

@dataclass(frozen=True, slots=True)
class KeywordHint:
    """A phrase that hints at a doc type, with a weight.

    Weight is the prior probability that a doc containing this phrase is
    of the given doc type. `0.95` = nearly definitive ("BILL OF LADING").
    `0.6` = supports the case ("INVOICE" — appears on PODs too).
    Negative weights would discourage; not used today.
    """

    phrase: str
    weight: float = 0.5


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    """A structured field the runtime should attempt to extract for this doc type."""

    name: FieldName
    required: bool
    extractor: ExtractorSpec


@dataclass(frozen=True, slots=True)
class DocTypeDefinition:
    """A doc type the KB knows about.

    `industries` is purely informational — it lets the install / setup
    flow filter the KB by what the customer says they do. The classifier
    itself doesn't use industry; doc types from any industry are eligible
    as long as their keywords match.
    """

    name: DocTypeName
    description: str
    industries: tuple[IndustryName, ...]
    keywords: tuple[KeywordHint, ...]
    fields: tuple[FieldDefinition, ...]


@dataclass(frozen=True, slots=True)
class VendorDefinition:
    """A known vendor and its overrides.

    Vendor-level entries override doc-type-level defaults: a Heineken BOL
    uses Heineken's `extractors[bol.bol_number]` instead of the generic
    BOL field extractor. This is how the system reflects the truth that
    *vendors differ even for the same doc type*.
    """

    canonical: VendorName
    aliases: tuple[str, ...]
    industries: tuple[IndustryName, ...]
    likely_doc_types: tuple[DocTypeName, ...]
    field_extractors: dict[str, ExtractorSpec] = field(default_factory=dict)
    # key format: "<doc_type>.<field_name>"  (e.g. "bol.bol_number")


@dataclass(slots=True)
class KnowledgeBase:
    """The merged in-memory view of all loaded packs + local learning.

    Mutable on purpose — the runtime adds entries here as the system
    learns from operator corrections. The base/pack YAMLs supply the
    starting state; the SQLite-backed learned overlay tops it up.
    """

    doc_types: dict[DocTypeName, DocTypeDefinition] = field(default_factory=dict)
    vendors: dict[VendorName, VendorDefinition] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)  # provenance for debugging


# ---------- Runtime results ----------

@dataclass(frozen=True, slots=True)
class Signal:
    """A single piece of evidence that contributed to a classification.

    Used in the review queue to explain *why* the system thinks a doc
    is a `bol`. Operators correct mistakes faster when they can see what
    misled the system.
    """

    kind: str          # "keyword" | "vendor" | "field_present" | "llm" | ...
    detail: str        # human-readable: "phrase 'BILL OF LADING'"
    contribution: float  # how much this added to the doc type score


@dataclass(frozen=True, slots=True)
class Classification:
    """The runtime's best guess for a document.

    Confidence is a calibrated 0.0–1.0 derived from the signal contributions
    and the gap to runner-up doc types. A near-tie produces low confidence
    even if the winner's absolute score is high.

    `alternatives` carries the top-N runners-up (typically 3) with their
    raw scores, so the inbox UI can show "if not this, then one of these"
    options and the operator picks an alternative with one click.
    """

    doc_type: DocTypeName | None
    vendor: VendorName | None
    fields: dict[FieldName, str]
    confidence: float
    signals: tuple[Signal, ...]
    runner_up: DocTypeName | None = None
    alternatives: tuple[tuple[DocTypeName, float], ...] = ()


@dataclass(frozen=True, slots=True)
class Correction:
    """An operator's correction of a classification.

    Sent from the review queue to the learning layer. The diff between
    the original classification and the correction is what generates rule
    updates (new keywords, new vendor aliases, new extractor specs).
    """

    document_id: str
    original: Classification
    corrected_doc_type: DocTypeName
    corrected_vendor: VendorName | None
    corrected_fields: dict[FieldName, str]
    operator_note: str | None = None
