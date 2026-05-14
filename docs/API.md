# HTTP API

All routes return JSON or HTML; redirects use 303 (See Other). When
`RUNTIME_ENGINE_AUTH_TOKEN` is set, every route except `/healthz` and
`/setup` requires `Authorization: Bearer <token>`.

## Health

### `GET /healthz`
Liveness probe. Returns `200 ok` text.

## Setup wizard

### `GET /setup`
HTML form for first-run config. If the wizard hasn't been completed,
`GET /` redirects here.

### `POST /setup`
Form fields: `watch_dir`, `destination_root`, `packs` (multi), `auto_create_folders`.
Persists to `data/runtime.yaml`, applies in process, then `303 → /`.

## Inbox

### `GET /`
Renders the inbox. Query params:
- `page` (default 1)
- `per_page` (default 50, max 200)
- `status_filter` ∈ {`pending`, `accepted`, `corrected`, `error`}

### `GET /stats` → JSON
```json
{
  "counts": {"pending": 4, "accepted": 27, "corrected": 3, "error": 0},
  "total": 34,
  "average_confidence": 0.74
}
```

## Per-classification actions

### `POST /classifications/{id}/accept` → 303
Mark a row accepted. No file move.

### `POST /classifications/{id}/correct` → 303
Form fields: `doc_type` (required, must be in KB), `vendor` (optional).
Moves the file to the corrected destination. Writes overlay rows.
Invalidates KB cache so subsequent classifications benefit.

### `POST /classifications/{id}/route-to` → JSON
Body: `{"folder_rel": "bol/2026"}`. Drag-to-folder endpoint. First path
segment becomes the corrected doc_type; vendor preserved from the
original classification.

### `POST /classifications/{id}/delete` → 303
Remove the log row. Does not touch the placed file.

### `POST /classifications/{id}/reprocess` → 303
Re-run the pipeline on the source file. Useful for error rows.

### `POST /classifications/bulk-accept` → JSON
Body: `{"ids":["...","..."]}`. Returns `{"accepted": N}`.

## Admin

### `POST /admin/reload-kb` → JSON
Force a fresh KB load from disk. Used after editing pack YAMLs.
Returns `{"doc_types": N, "vendors": M, "sources": [...]}`.
