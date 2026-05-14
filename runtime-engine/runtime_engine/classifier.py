"""Document classification — walk the KB, return a typed Classification.

Pure function with no side effects: `classify(ocr_text, kb)`. The runtime
wiring (OCR, route, queue) lives elsewhere; this module is the brain.

Algorithm (today; intentionally simple):
1. Uppercase the text once.
2. For each doc type, sum the weights of keyword hints whose phrase is
   present. That's the doc type's raw score.
3. Look for vendor aliases in the text; the best-matching vendor wins.
   Vendor presence biases scores toward that vendor's `likely_doc_types`.
4. Top-scoring doc type wins. Confidence is its score divided by
   (score + runner_up_score + a smoothing constant), so a runaway leader
   converges to ~1.0 and a near-tie sits near 0.5.
5. Signals list documents what contributed, for the review-queue UI.

This is deliberately not a neural net. We want the runtime to be free,
deterministic, explainable, and easy to update from corrections.
"""

from __future__ import annotations

from shared.types import (
    Classification,
    DocTypeName,
    KnowledgeBase,
    Signal,
    VendorName,
)

# Vendor presence nudges scores toward that vendor's likely doc types.
# Small bonus on purpose — the keyword evidence should still dominate.
_VENDOR_BIAS = 0.20

# Smoothing in the confidence calc. Higher = more conservative confidence.
_CONFIDENCE_SMOOTHING = 0.5


def _find_vendor(text_upper: str, kb: KnowledgeBase) -> VendorName | None:
    """Return the canonical name of the first KB vendor whose alias appears.

    Aliases are matched as case-insensitive substrings. We check the
    canonical name and all aliases. Order across the KB dict is insertion
    order; packs that load earlier (base, then industries) take priority
    over later-merged learnings.
    """
    for vendor in kb.vendors.values():
        candidates = (vendor.canonical, *vendor.aliases)
        for alias in candidates:
            if alias.upper() in text_upper:
                return vendor.canonical
    return None


def _score_doc_types(
    text_upper: str, kb: KnowledgeBase, vendor: VendorName | None
) -> tuple[dict[DocTypeName, float], list[Signal]]:
    """Return doc-type → cumulative score and the signals that built them up."""
    scores: dict[DocTypeName, float] = {}
    signals: list[Signal] = []

    for dt in kb.doc_types.values():
        score = 0.0
        for hint in dt.keywords:
            if hint.phrase.upper() in text_upper:
                score += hint.weight
                signals.append(
                    Signal(
                        kind="keyword",
                        detail=f"{dt.name}: phrase {hint.phrase!r}",
                        contribution=hint.weight,
                    )
                )
        if score > 0:
            scores[dt.name] = score

    if vendor is not None:
        v = kb.vendors[vendor]
        for dt_name in v.likely_doc_types:
            if dt_name in scores:
                scores[dt_name] += _VENDOR_BIAS
                signals.append(
                    Signal(
                        kind="vendor",
                        detail=f"{vendor} is associated with {dt_name}",
                        contribution=_VENDOR_BIAS,
                    )
                )

    return scores, signals


def _confidence(top_score: float, runner_up_score: float) -> float:
    """Map (top, runner_up) → 0.0–1.0 confidence.

    Pure ratio runs hot — a 1.0 vs 0.0 split shouldn't give 100%
    confidence with only one matched keyword. Smoothing in the
    denominator pulls early high-ratio results toward the middle until
    more evidence accumulates.
    """
    if top_score <= 0:
        return 0.0
    return top_score / (top_score + runner_up_score + _CONFIDENCE_SMOOTHING)


def classify(ocr_text: str, kb: KnowledgeBase) -> Classification:
    """Classify a document from its OCR text against the loaded KB.

    Returns a Classification with `doc_type=None` and `confidence=0.0`
    when no doc type matched any keyword. Callers should treat that as
    "send to LLM fallback or review queue."
    """
    if not ocr_text.strip():
        return Classification(
            doc_type=None, vendor=None, fields={}, confidence=0.0, signals=()
        )

    text_upper = ocr_text.upper()
    vendor = _find_vendor(text_upper, kb)
    scores, signals = _score_doc_types(text_upper, kb, vendor)

    if not scores:
        return Classification(
            doc_type=None,
            vendor=vendor,
            fields={},
            confidence=0.0,
            signals=tuple(signals),
        )

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_name, top_score = ranked[0]
    runner_up_name, runner_up_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))
    # Up to 3 alternatives (positions 2, 3, 4 — skip the winner itself).
    alternatives = tuple(ranked[1:4])

    return Classification(
        doc_type=top_name,
        vendor=vendor,
        fields={},  # populated by extractor in a later pass
        confidence=_confidence(top_score, runner_up_score),
        signals=tuple(signals),
        runner_up=runner_up_name,
        alternatives=alternatives,
    )
