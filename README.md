# Doc Sherpa

A self-hosted document classification and routing pipeline. Doc Sherpa
guides scanned documents to where they belong. The pitch: anyone should be
able to stand this up in under an hour, point it at a folder of scans, and
have correctly-named PDFs landing in their destination of choice — without
ever writing a regex.

## Two services

### `training-service/`
A small FastAPI app. A customer uploads 20–30 sample documents, labels them
once, and Claude iterates regex patterns until the classifier matches their
labels. Output: a `classifier.yaml` they deploy to their runtime engine.

This is the differentiating piece. Most of the value lives here.

### `runtime-engine/` *(phase 2 — not built yet)*
A daemon that watches a folder, OCRs incoming PDFs, classifies them using the
trained `classifier.yaml`, and routes them via plugins (local filesystem, S3,
Google Drive, SharePoint). Intentionally commodity infrastructure.

## Plugin categories (priority-ordered)

1. **DocumentDestination** — filesystem → S3 → Google Drive → SharePoint
2. **AIClassifier** — Anthropic Claude (used by both services)
3. **NotificationSink** — webhook (Slack/generic), email later
4. **DocumentIngest** — watch folder → HTTP upload API

Protocols live in `shared/protocols/`. Implementations live in `plugins/`.

## Running the training service (phase 1)

```bash
python -m venv .venv
source .venv/Scripts/activate    # Windows / Git-Bash
pip install -e .[dev]

uvicorn training_service.main:app --app-dir training-service --reload --port 8001
```

Then visit <http://localhost:8001/> in a browser.

## Project layout

```
doc-sherpa/
├── training-service/      # phase 1 (in progress)
│   └── training_service/  # the importable Python package
├── runtime-engine/        # phase 2
├── shared/                # plugin Protocols + shared types
│   └── protocols/
├── packs/                 # industry pack YAML (beverage_distributor, etc.)
├── plugins/               # plugin implementations
├── docs/                  # design notes
└── tests/
```

Directory names use hyphens (`training-service/`) per the project convention;
Python packages inside use underscores (`training_service/`) because hyphens
are not valid Python identifiers.

## Status

| Phase | Scope                                | Status      |
|------:|--------------------------------------|-------------|
| 1     | Training service (upload → YAML)     | in progress |
| 2     | Runtime engine (watch → OCR → route) | not started |
| 3     | Setup wizard, packs, polish          | not started |

See `CLAUDE.md` for the full design brief.
