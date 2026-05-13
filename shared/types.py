"""Shared data types — document categories, labels, classification results.

These types travel between services and across the plugin boundary, so they
live in `shared/` rather than either service's package. Keep this file
small; if it starts mixing concerns, split.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DocumentType(str, Enum):
    """The five document categories the classifier knows about.

    Vocabulary borrowed from the production scan-to-sharepoint deployment at
    Kristen Distributing. Order is significant in `DOC_TYPE_RULES` (more
    distinctive types check first); that *order* should be re-learned per
    customer by the training service, but the *vocabulary* is fixed for the
    POC. New customers in adjacent industries may need additional types —
    treat that as a phase-3 concern.
    """

    TRADE_OUT = "trade_out"
    BOL = "bol"
    POD = "pod"
    DONATION = "donation"
    INVOICE = "invoice"
    OTHER = "other"  # escape hatch for the labeling UI; not part of trained output


@dataclass(frozen=True, slots=True)
class Label:
    """A single operator-supplied label for a training document.

    All fields besides `doc_type` are optional because not every document has
    every field — a trade-out form rarely has an invoice number, a donation
    form has no vendor distinct from the donor. Empty strings are normalized
    to None at the persistence boundary.
    """

    doc_type: DocumentType
    vendor: str | None = None
    identifier: str | None = None  # invoice number, BOL number — type-dependent
    customer: str | None = None


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Output of running the classifier against a single document.

    `confidence` is the classifier's self-reported score; the routing layer
    decides whether confidence is high enough to skip human review. The
    training service produces this same shape when scoring its own output
    against held-back validation labels.
    """

    doc_type: DocumentType
    vendor: str | None
    identifier: str | None
    customer: str | None
    confidence: float  # 0.0 - 1.0
