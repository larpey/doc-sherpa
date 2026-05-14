"""Claude AIClassifier — the LLM fallback for novel documents.

Used when keyword-based classification confidence is too low. The plugin
returns a `Classification` (or None on any failure mode); the pipeline
caller merges it with the keyword result and uses the higher-confidence
of the two.

Cheap by default: uses `claude-haiku-4-5` since the long-tail volume is
high and per-doc cost compounds. Operators can upgrade to Sonnet via
env var if they value accuracy over cost.

No retries. A fallback that fails just falls through to whatever the
regex-based path produced.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from shared.types import Classification, Signal

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_OUTPUT_TOKENS = 400
_INPUT_CHAR_CAP = 6000  # Crop OCR text — first page typically holds the headers we need

_SYSTEM_PROMPT = (
    "You are extracting document metadata from OCR text. Given a list of "
    "possible doc types from a knowledge base, decide which one this "
    "document is, identify its vendor (the company that issued it), and "
    "extract any obvious identifying numbers. If you cannot identify a "
    "field with confidence >= 0.7, set it to null. Return ONLY a single "
    "JSON object — no prose, no markdown fences.\n\n"
    "Shape:\n"
    "{\n"
    '  "doc_type": "<one of the provided names, or null>",\n'
    '  "vendor": "<canonical issuer name, or null>",\n'
    '  "identifier": "<best-guess primary reference number, or null>",\n'
    '  "confidence": <float 0.0-1.0>\n'
    "}\n"
)


def is_available() -> bool:
    """Whether the Anthropic API key is set so we can call out at all."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class ClaudeClassifier:
    """LLM-backed classifier. Returns None on any failure mode."""

    name = "claude"

    def __init__(self, model: str | None = None) -> None:
        self._model = model or os.environ.get("RUNTIME_ENGINE_LLM_MODEL", _DEFAULT_MODEL)

    def classify(
        self, ocr_text: str, known_vendors: list[str] | None = None, known_doc_types: list[str] | None = None
    ) -> Classification | None:
        """Ask Claude. Returns None for any error.

        `known_doc_types` is the vocabulary we want Claude to pick from
        — passing the active KB's vocabulary keeps the LLM honest about
        what the system can route. Without it, Claude invents categories.
        """
        if not is_available():
            return None
        if not ocr_text.strip():
            return None

        # Lazy import: the SDK is optional at install time; the system
        # works without it as long as nobody calls into the plugin.
        try:
            import anthropic
        except ImportError:
            logger.warning("anthropic SDK not installed; LLM fallback disabled")
            return None

        cropped = ocr_text[:_INPUT_CHAR_CAP]
        vocabulary_hint = (
            "Possible doc types: " + ", ".join(known_doc_types or [])
            if known_doc_types
            else "Pick any reasonable doc type name."
        )
        known_vendor_hint = (
            "Known vendors that may appear: " + ", ".join(known_vendors or [])
            if known_vendors
            else ""
        )

        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=self._model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"{vocabulary_hint}\n{known_vendor_hint}\n\n"
                            f"--- OCR TEXT ---\n{cropped}\n--- END ---"
                        ),
                    }
                ],
            )
        except Exception:  # noqa: BLE001 — any network / auth / rate-limit
            logger.exception("LLM classify call failed")
            return None

        try:
            text_block = next(
                (b.text for b in response.content if getattr(b, "type", None) == "text"),
                None,
            )
            if not text_block:
                return None
            parsed: dict[str, Any] = json.loads(text_block.strip().strip("`"))
        except (json.JSONDecodeError, AttributeError, StopIteration):
            logger.warning("LLM returned unparseable JSON")
            return None

        doc_type = parsed.get("doc_type")
        if doc_type is not None and not isinstance(doc_type, str):
            return None
        vendor = parsed.get("vendor")
        identifier = parsed.get("identifier")
        confidence = float(parsed.get("confidence") or 0.0)

        fields: dict[str, str] = {}
        if identifier and isinstance(identifier, str):
            # Generic field name; the routing layer falls back to this
            # for the {identifier} template variable.
            fields["llm_identifier"] = identifier

        return Classification(
            doc_type=doc_type,
            vendor=vendor if isinstance(vendor, str) else None,
            fields=fields,
            confidence=max(0.0, min(1.0, confidence)),
            signals=(Signal(kind="llm", detail=f"{self._model}", contribution=confidence),),
            runner_up=None,
            alternatives=(),
        )
