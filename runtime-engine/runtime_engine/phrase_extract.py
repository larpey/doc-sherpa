"""Pull distinctive phrases out of OCR text for the learning loop.

When the operator corrects a classification, we need to figure out
*which words* in the doc should now be associated with the corrected
doc type. This module does that with simple heuristics — no NLP
dependency.

Strategy:
1. Find UPPERCASE header-style phrases (2–4 consecutive uppercase
   words). These are typically titles like "BILL OF LADING",
   "PURCHASE ORDER", "RX". High signal-to-noise.
2. Skip common boilerplate ("DATE", "PAGE", "TOTAL", etc.) that
   appears across doc types.
3. Limit output to a small number (~5–8) per correction so a single
   correction can't flood the overlay.

This is intentionally simple. A better version uses TF-IDF across all
seen docs to find genuinely *distinctive* phrases per doc type. POC
gets by with the header-phrase heuristic.
"""

from __future__ import annotations

import re

# Phrases that appear on almost every business document — adding them
# as keyword hints would teach the classifier nothing. We skip these.
_BOILERPLATE: frozenset[str] = frozenset(
    {
        "DATE", "TOTAL", "PAGE", "PAGE OF", "PAGE 1 OF 1",
        "QTY", "DESCRIPTION", "AMOUNT", "PRICE",
        "FROM", "TO", "CC",
        "NAME", "PHONE", "FAX", "EMAIL",
        "ADDRESS", "CITY", "STATE", "ZIP",
        "USA", "INC", "LLC", "CORP", "LTD",
        "THANK YOU",
        # Common single words that are too generic
        "INVOICE", "TOTAL DUE",  # covered by pack-level rules already
    }
)

# Matches 2–4 consecutive UPPERCASE words (letters + digits OK, but
# starting with a letter). Allows ampersands and hyphens because real
# business names have them ("MOLSON COORS", "ANHEUSER-BUSCH").
_HEADER_RE = re.compile(
    r"\b([A-Z][A-Z0-9&\-]+(?:\s+[A-Z][A-Z0-9&\-]+){1,3})\b"
)

# Stand-alone single-word RX-style tokens worth promoting even though the
# multi-word regex misses them. Add new ones cautiously.
_SHORT_DISTINCTIVE = re.compile(r"\b(RX|EOB|COI|RFI|W-?9|W-?2|1099|K-?1|CMS-?1500)\b")


def extract_phrases(text: str, *, max_phrases: int = 8) -> list[str]:
    """Return up to `max_phrases` candidate phrases ordered by length, deduped.

    Longer phrases come first because they carry more signal —
    "BILL OF LADING" is more distinctive than "BILL".
    """
    seen: set[str] = set()
    candidates: list[str] = []

    for match in _HEADER_RE.finditer(text):
        phrase = " ".join(match.group(1).split())
        if phrase in _BOILERPLATE:
            continue
        if phrase in seen:
            continue
        seen.add(phrase)
        candidates.append(phrase)

    for match in _SHORT_DISTINCTIVE.finditer(text):
        phrase = match.group(1).upper()
        if phrase in seen:
            continue
        seen.add(phrase)
        candidates.append(phrase)

    # Longest first — they're more specific.
    candidates.sort(key=lambda p: (-len(p), p))
    return candidates[:max_phrases]
