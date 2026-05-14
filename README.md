# Doc Sherpa

Self-hosted document classification and routing. Drop a scan in a folder.
The system reads it, figures out what it is, names it correctly, and files
it where it belongs.

**No regex to write. No labeled corpus required. Day-1 useful, gets better
as you use it.**

## How it works

1. **Pre-trained knowledge base** ships with the binary — common doc types
   (invoice, receipt, BOL, PoD, prescription, …) and known vendor patterns
   across logistics, healthcare, and general business documents.
2. **Watch folder** picks up new PDFs. OCR if scanned, direct text extraction
   if born-digital.
3. **Classifier** scores against the KB and produces `(doc_type, vendor,
   identifier, confidence)`. High confidence → routed silently. Low
   confidence → review queue.
4. **LLM fallback** (Claude Haiku) handles documents nothing in the KB
   recognizes. Optional — system runs fine without it.
5. **Review queue** is a one-page web UI. Operator accepts or corrects
   each uncertain classification. Drag a doc onto a folder to file it.
6. **Learning loop** turns corrections into local KB additions. The next
   similar document benefits — no retraining step, no rule editing.

## Quick start (Docker, recommended)

```bash
git clone https://github.com/larpey/doc-sherpa.git
cd doc-sherpa

# Optional: enable LLM fallback
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
# Optional: require auth on the HTTP surface
echo "RUNTIME_ENGINE_AUTH_TOKEN=$(openssl rand -hex 32)" >> .env

docker compose up -d
```

Visit <http://localhost:8002>, complete the one-screen wizard (watch folder,
destination root, which industry packs to activate), then drop PDFs in
`./scans/incoming/`. They appear in the inbox within ~2 seconds.

## Quick start (direct, for development)

```bash
python -m venv .venv
. .venv/Scripts/activate          # or source .venv/bin/activate on POSIX
pip install -e .[dev]

# Install tesseract for scanned-PDF OCR (born-digital works without it):
#   Windows: choco install tesseract
#   macOS:   brew install tesseract
#   Linux:   apt install tesseract-ocr ghostscript

uvicorn runtime_engine.main:app --app-dir runtime-engine --reload --port 8002
```

## Architecture

Two services, both runnable independently:

- **`runtime-engine/`** — the main app. Watch → OCR → classify → route →
  log. One-page inbox UI for accept/correct. Continuous learning from
  corrections.
- **`training-service/`** — bootstrap label-and-upload UI. Optional;
  most installs never use it because the pre-trained KB + review-queue
  learning loop covers cold-start.

Plus:

- **`shared/`** — plugin Protocols + cross-service types.
- **`packs/`** — knowledge packs (`_base.yaml`, `logistics.yaml`,
  `healthcare.yaml`). Pure YAML, hot-reload on edit.
- **`plugins/`** — concrete plugin implementations:
  - `document_destination/filesystem` — local filesystem (built-in)
  - `ai_classifier/claude` — Anthropic Claude (Haiku by default)
  - `notification_sink/webhook` — generic JSON-POST notifications
- **`docs/`** — `ARCHITECTURE.md`, `API.md`, `DEPLOYMENT.md`.
- **`deployment/`** — systemd unit.

See `docs/ARCHITECTURE.md` for the full module breakdown.

## What works today

| Feature | Status |
|---|---|
| Born-digital PDF classification | ✅ |
| Scanned PDF classification (OCR via tesseract) | ✅ |
| Filesystem destination | ✅ |
| Anthropic Claude LLM fallback | ✅ (requires API key) |
| Review queue with drag-to-folder | ✅ |
| Learning loop (corrections → KB updates) | ✅ |
| Bearer-token auth | ✅ |
| Multi-industry packs (base + logistics + healthcare) | ✅ |
| Auto-confirm above confidence threshold | ✅ |
| Content-hash deduplication | ✅ |
| Pack hot-reload on file edit | ✅ |
| Docker + systemd deployment | ✅ |
| S3 / Google Drive / SharePoint destinations | not built |
| Archive ingestion mode | not built |
| Multi-page PDF splitting | not built |
| Layout-aware extraction (CMS-1500, W-2, etc.) | not built |

## HTTP API (summary)

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Inbox (HTML) |
| GET | `/healthz` | Liveness probe |
| GET | `/stats` | Counts per status + average confidence |
| GET | `/setup` | First-run wizard (HTML) |
| POST | `/setup` | Persist wizard config |
| POST | `/classifications/{id}/accept` | Confirm classification |
| POST | `/classifications/{id}/correct` | Change doc type / vendor, move file |
| POST | `/classifications/{id}/route-to` | Drag-to-folder endpoint (JSON) |
| POST | `/classifications/{id}/delete` | Remove a row |
| POST | `/classifications/{id}/reprocess` | Re-run pipeline (for error rows) |
| POST | `/classifications/bulk-accept` | Accept many at once |
| POST | `/admin/reload-kb` | Force a KB reload |

Full request/response shapes in `docs/API.md`.

## Configuration

Environment variables (full list in `docs/DEPLOYMENT.md`):

| Var | Default | |
|---|---|---|
| `RUNTIME_ENGINE_WATCH_DIR` | `runtime-engine/incoming` | Where new PDFs arrive |
| `RUNTIME_ENGINE_DESTINATION_ROOT` | `runtime-engine/classified` | Where classified PDFs go |
| `RUNTIME_ENGINE_AUTH_TOKEN` | (unset) | Set to require Bearer auth |
| `RUNTIME_ENGINE_CONFIDENCE_THRESHOLD` | `0.30` | Below → unclassified |
| `RUNTIME_ENGINE_AUTO_CONFIRM_THRESHOLD` | `0.85` | Above → silent file |
| `ANTHROPIC_API_KEY` | (unset) | Enables LLM fallback |

## Testing

```bash
pytest                  # 74 tests, all in-process
pytest -q tests/runtime_engine/test_pipeline.py
```

Tests run against synthetic PDF fixtures generated by `reportlab`. Real
scanned PDFs are out-of-scope for the test suite (would need a tesseract
install on CI); the OCR path is covered via a fake plugin.

## Project conventions

- One responsibility per module. If naming the file needs "and," split it.
- Type hints everywhere; `Any` only with an inline justification comment.
- Plugin interface tests use fakes, not mocks.
- Public functions have docstrings explaining *intent*, not what.
- Directory names use hyphens (`runtime-engine/`); Python packages inside
  use underscores (`runtime_engine/`).

See `CLAUDE.md` for the full design brief.

## License

Proprietary.
