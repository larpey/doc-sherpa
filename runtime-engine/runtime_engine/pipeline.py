"""End-to-end pipeline: text → classify → extract → route → place.

One function: `process_document(path, kb, config, destination)`.

The pipeline is intentionally synchronous and pure-ish — no I/O of its
own beyond what each step does. Side effects live in their owners:
- text extraction touches the source PDF
- the destination plugin touches the target filesystem / cloud
- the (later) DB persistence writes the classification log

Adding async / batch / retry concerns at the pipeline level would mix
the orchestration with the substrate. Keep this layer thin.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from shared.protocols.document_destination import DestinationError
from shared.types import Classification, KnowledgeBase

from .classifier import classify
from .extractor import extract_fields
from .router import RoutingConfig, is_unclassified, render_destination
from .text_extract import extract_text


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Outcome of processing one document.

    `final_path` is the destination plugin's identifier (a local path for
    filesystem, a URL for cloud destinations). `was_unclassified` is true
    when the doc went to the unclassified folder — used by the review
    queue UI to know what to surface.
    """

    source_path: Path
    classification: Classification
    destination_path: str | None
    final_path: str | None
    was_unclassified: bool
    error: str | None = None


def process_document(
    source_path: Path,
    kb: KnowledgeBase,
    config: RoutingConfig,
    destination,  # DocumentDestination — structural; avoid circular type hint
) -> ProcessResult:
    """Run one document through the full pipeline. Returns a ProcessResult.

    Errors do not raise — they're packaged into `ProcessResult.error` so
    the calling loop (watcher / batch processor) can decide whether to
    retry, alert, or move on without crashing.
    """
    try:
        text = extract_text(source_path)
    except Exception as exc:  # noqa: BLE001 — top of pipeline defensive boundary
        empty_classification = Classification(
            doc_type=None, vendor=None, fields={}, confidence=0.0, signals=()
        )
        return ProcessResult(
            source_path=source_path,
            classification=empty_classification,
            destination_path=None,
            final_path=None,
            was_unclassified=True,
            error=f"text extraction failed: {exc}",
        )

    classification = classify(text, kb)
    if classification.doc_type:
        fields = extract_fields(text, kb, classification.doc_type, classification.vendor)
        classification = replace(classification, fields=fields)

    dest_path = render_destination(classification, source_path, config)

    try:
        final_path = destination.place(source_path, dest_path)
    except DestinationError as exc:
        return ProcessResult(
            source_path=source_path,
            classification=classification,
            destination_path=dest_path,
            final_path=None,
            was_unclassified=is_unclassified(classification, config),
            error=str(exc),
        )

    return ProcessResult(
        source_path=source_path,
        classification=classification,
        destination_path=dest_path,
        final_path=final_path,
        was_unclassified=is_unclassified(classification, config),
        error=None,
    )
