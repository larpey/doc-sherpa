"""Runtime engine: watch → OCR → classify → route.

Day-1-autonomous: ships with a pre-trained knowledge base, falls back to
LLM for novel documents, learns from operator corrections in the review
queue. No discrete training step.

This package owns:
- KB loading and merging (`kb`)
- Classification (`classifier`)
- Field extraction (`extractor`)
- (later) watch, OCR, route, review queue
"""
