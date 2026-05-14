# Architecture

Doc Sherpa is two services in one repo. Both are independently runnable.

## Top-level layout

```
doc-sherpa/
├── runtime-engine/      the main app (watch → OCR → classify → route)
├── training-service/    bootstrap-only label UI (becomes optional once
│                        the runtime is learning on its own)
├── shared/              cross-service types + Protocols
├── packs/               YAML knowledge packs (base + per-industry)
├── plugins/             concrete plugin implementations
├── tests/               pytest suites for both services + fixtures
└── docs/                this file + API.md + DEPLOYMENT.md
```

## Data flow

```
        ┌─────────────┐                ┌─────────────────┐
        │  PDF lands  │                │  Operator opens │
        │ in watcher  │                │     /inbox      │
        └──────┬──────┘                └────────┬────────┘
               │                                │
               ▼                                ▼
        ┌─────────────┐                ┌─────────────────┐
        │  text       │                │  list_recent    │
        │  extract    │                │  with pagination│
        └──────┬──────┘                └────────┬────────┘
               │                                │
        born-digital? ────► no ────► OCR ─┐    │
               │ yes                       │    │
               ▼                           ▼    │
        ┌─────────────┐               ┌────────────┐
        │  classify   │ ◄────────────│  KB =       │
        │  (keywords) │               │  packs +    │
        └──────┬──────┘               │  overlay    │
               │                       └─────────────┘
        confidence?                          ▲
       ╱        ╲                            │
   high          low                         │
    │             │                          │
    │      LLM fallback (if key)             │
    │             │                          │
    ▼             ▼                          │
  extract fields ──► route ──► place ──► log │
                                          │  │
                                          ▼  │
                            ┌─────────────────┐
                            │ operator        │
                            │ accept/correct  │
                            └────────┬────────┘
                                     │
                              corrections write
                              overlay rows ─────────┘
```

## Module responsibilities

| Module | Responsibility |
|---|---|
| `runtime_engine.text_extract` | pypdf-first, OCR fallback |
| `runtime_engine.ocr` | ocrmypdf shell-out |
| `runtime_engine.classifier` | keyword-weighted doc-type scoring + confidence |
| `runtime_engine.extractor` | compile + run extractor specs |
| `runtime_engine.router` | classification + template → relative path |
| `runtime_engine.pipeline` | text→classify→extract→route→place |
| `runtime_engine.watcher` | polling-scan loop, content-hash dedup, stability check |
| `runtime_engine.log` | classifications SQLite CRUD |
| `runtime_engine.overlay` | learned keywords + vendors, merged into KB |
| `runtime_engine.learning` | turn corrections into overlay rows |
| `runtime_engine.phrase_extract` | distinctive phrases for the learning loop |
| `runtime_engine.kb` | YAML pack load + merge |
| `runtime_engine.deps` | lazy singletons (KB, destination, fallback, routing) |
| `runtime_engine.routes` | HTTP wiring |
| `runtime_engine.templates` | inbox HTML |
| `runtime_engine.wizard` | first-run config persistence |
| `runtime_engine.auth` | bearer-token middleware |
| `runtime_engine.hashing` | SHA-256 of file content |
| `runtime_engine.settings` | pydantic-settings config |
| `runtime_engine.main` | FastAPI app + lifespan |

## Plugin Protocols

`shared/protocols/`. Each Protocol gets one file with intent docstring.

- **DocumentDestination** — `place(source, destination_path) → str`
  - `plugins/document_destination/filesystem.py` (implemented)
- **AIClassifier** — `classify(text, known_vendors=, known_doc_types=) → Classification | None`
  - `plugins/ai_classifier/claude.py` (implemented)
- **NotificationSink** — `notify(level, message, *, context=) → None`
  - `plugins/notification_sink/webhook.py` (implemented)
- **DocumentIngest** — `start(callback) / stop()` (Protocol only; only the watch-folder ingestion is built into `watcher.py`)

## The KB / overlay

`KB` is the merged view of packs + overlay. Both live in code at runtime; the operator never reads YAML or writes SQL.

- **Packs** ship with the binary, define common doc types and vendor signatures per industry. Edited by the project, not the customer.
- **Overlay** is per-deployment SQLite. Born when the operator corrects a classification. Append-only (we don't *remove* learnings; if a learning is wrong, the next correction adds a counter-signal).

The classifier always reads the merged view. The pipeline never sees the seam.

## Why each tech choice

- **SQLite** (not Postgres): single-tenant POC, schema is 4 tables, no migrations. Postgres pays off when we add multi-user.
- **pypdf** (not pdfplumber): faster, smaller, good enough for our use of text-only extraction.
- **ocrmypdf** (not raw tesseract): handles deskew / language / PDF-A output. Tesseract is its engine.
- **Pydantic Settings** (not click): one source of truth for env vars + YAML overrides.
- **Inline HTML f-strings** (not Jinja): 4 pages. Jinja is the next step, not the current one.
- **Polling watcher** (not watchdog): Windows + SMB make file-system events flaky; a 2-second polling loop is universally reliable.

## What's intentionally NOT built

- **Multi-tenancy.** One install per customer.
- **Layout-aware extraction** (LayoutLM-style). Single-document text classification covers the common case; structured forms come later.
- **Multi-page splitting.** A scanned stack of N invoices comes in as one file; the system classifies it as one doc. Splitting needs page-boundary detection — a separate research project.
- **Cloud destination plugins** (S3, Drive, SharePoint). Protocols exist; impls don't.
- **Cross-customer learning.** The overlay is local. Federated learning across deployments is interesting but introduces privacy / data-egress complexity we want to defer.
