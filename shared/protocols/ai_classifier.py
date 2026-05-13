"""AIClassifier Protocol: an LLM-backed classifier for low-confidence cases.

Used by both services:

- The **training service** uses it to synthesize regex patterns from labeled
  samples (Claude Sonnet, expensive but rare — one call per training run).
- The **runtime engine** uses it as a fallback when the regex-based
  classifier cannot identify the vendor or BOL number (Claude Haiku, cheap
  and frequent — one call per low-confidence document).

The shape is the same; the model and prompt differ. Concrete implementations
live in `plugins/ai_classifier/`. POC plugin: `claude.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from shared.types import ClassificationResult


@runtime_checkable
class AIClassifier(Protocol):
    """An AI model that can classify or extract fields from OCR'd document text."""

    name: str
    """Stable identifier (e.g. `"anthropic-claude"`)."""

    def classify(self, ocr_text: str, known_vendors: list[str] | None = None) -> ClassificationResult | None:
        """Classify a document from its OCR text.

        Args:
            ocr_text: The text extracted from the document by OCR. The plugin
                is responsible for any cropping or token-budgeting it needs.
            known_vendors: Optional list of canonical vendor names the caller
                already knows about. Helps the model snap to existing
                conventions instead of inventing new vendor spellings.

        Returns:
            A `ClassificationResult` on success, or `None` on any
            unrecoverable failure (timeout, auth error, malformed response,
            daily cap exhausted). Callers should treat `None` as "the regex
            path is the ground truth" and route to the review queue.

        Notes:
            The Protocol intentionally does not expose token counts, cost,
            or model name in the return value — those belong in structured
            logs the plugin emits, not in the data path. Adding fields here
            is one of the first ways the abstraction gets corrupted.
        """
        ...
