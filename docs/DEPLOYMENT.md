# Deployment

Three supported deployment modes. Pick whichever matches your stack.

## 1. Docker (recommended)

```bash
docker compose up -d
# Visit http://localhost:8002, complete the wizard.
```

Edit `docker-compose.yml` to:
- Mount `./scans/incoming` and `./scans/classified` somewhere persistent
- Set `ANTHROPIC_API_KEY` to enable LLM fallback
- Set `RUNTIME_ENGINE_AUTH_TOKEN` to require auth

The image bundles **tesseract** and **ghostscript**, so OCR works out of the box.

## 2. systemd (Linux host install)

```bash
sudo useradd -r doc-sherpa
sudo mkdir -p /opt/doc-sherpa /var/lib/doc-sherpa
sudo chown doc-sherpa:doc-sherpa /var/lib/doc-sherpa

# Copy the repo
sudo cp -r . /opt/doc-sherpa/

# Set up venv
cd /opt/doc-sherpa
sudo -u doc-sherpa python3 -m venv .venv
sudo -u doc-sherpa .venv/bin/pip install -e .

# Install tesseract
sudo apt install tesseract-ocr ghostscript

# Install + enable service
sudo cp deployment/doc-sherpa.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now doc-sherpa
```

## 3. Direct (development / quick start)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Install tesseract (see below)

uvicorn runtime_engine.main:app --app-dir runtime-engine --reload --port 8002
```

## OCR engine

Doc Sherpa ships with two OCR backends; pick one. Default is **RapidOCR**
(faster, multi-threaded, no system deps).

### RapidOCR (default, recommended)

ONNX-runtime based. Multi-threaded per call (uses all CPU cores). ~100 MB
install (model files cached after first run). Cross-platform, no system
binaries needed.

```bash
pip install -e .[rapidocr]
# or in the Docker image: included by default.
```

### Tesseract (opt-in, legacy / fallback)

Single-threaded. Mature. Requires the OS-level tesseract binary on PATH.

- **Windows:** `choco install tesseract` or <https://github.com/UB-Mannheim/tesseract/wiki>
- **macOS:** `brew install tesseract`
- **Debian/Ubuntu:** `apt install tesseract-ocr ghostscript`
- **Alpine:** `apk add tesseract-ocr ghostscript`

```bash
pip install -e .[tesseract]
```

Set `RUNTIME_ENGINE_OCR_ENGINE=tesseract` to force it. Default `auto`
picks RapidOCR if installed, else tesseract, else logs a warning and
sends scanned PDFs to `unclassified/`.

## Env vars

| Var | Default | Notes |
|---|---|---|
| `RUNTIME_ENGINE_WATCH_DIR` | `runtime-engine/incoming` | Where new PDFs arrive |
| `RUNTIME_ENGINE_DESTINATION_ROOT` | `runtime-engine/classified` | Where classified PDFs go |
| `RUNTIME_ENGINE_DB_PATH` | `runtime-engine/data/runtime.db` | SQLite file |
| `RUNTIME_ENGINE_ACTIVE_PACKS` | `logistics,healthcare` | Industry packs to load |
| `RUNTIME_ENGINE_AUTH_TOKEN` | (unset) | Set to require Bearer auth |
| `RUNTIME_ENGINE_CONFIDENCE_THRESHOLD` | `0.30` | Below → unclassified folder |
| `RUNTIME_ENGINE_AUTO_CONFIRM_THRESHOLD` | `0.85` | Above → silent file |
| `RUNTIME_ENGINE_AUTO_CREATE_FOLDERS` | `true` | Pre-existing folders only if `false` |
| `RUNTIME_ENGINE_POLL_INTERVAL_SECONDS` | `2.0` | How often watcher scans |
| `RUNTIME_ENGINE_LLM_MODEL` | `claude-haiku-4-5-20251001` | LLM fallback model |
| `RUNTIME_ENGINE_OCR_ENGINE` | `auto` | `auto` \| `rapidocr` \| `tesseract` |
| `RUNTIME_ENGINE_WATCHER_PARALLELISM` | `1` | Concurrent docs per scan pass (set to core count) |
| `ANTHROPIC_API_KEY` | (unset) | Required for LLM fallback |

## Throughput tuning

The watcher processes documents sequentially by default. On a multi-core
VPS, raise `RUNTIME_ENGINE_WATCHER_PARALLELISM` to the core count to
process N PDFs concurrently. Combined with RapidOCR's multi-threaded
inference, this typically gives 3–4× throughput on a 4-core box vs. the
single-threaded tesseract default.

## Backup

The SQLite file at `RUNTIME_ENGINE_DB_PATH` holds everything mutable:
- classification log
- learned overlay (keywords + vendors)
- wizard config

Back it up with whatever you use for the rest of your data — `litestream`
to S3 is a good no-touch option.

## Health monitoring

```bash
curl -fsS http://localhost:8002/healthz   # liveness
curl -fsS http://localhost:8002/stats     # counts + accuracy
```
