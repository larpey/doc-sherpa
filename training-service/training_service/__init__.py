"""Training service: upload sample docs, label them, get a classifier.yaml.

The customer-facing piece of the POC. Most of the product value lives here;
the runtime engine is intentionally commodity downstream of this.

Phase 1 scope (this package today):
- Upload endpoint
- Labeling UI
- Session persistence (SQLite)
- 24h cleanup (stub)

Phase 1 next (not built yet):
- Regex synthesis via Claude Sonnet
- YAML output endpoint
"""
