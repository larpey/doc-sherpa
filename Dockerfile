# Multi-stage Dockerfile for the Doc Sherpa runtime engine.
#
# Stage 1 builds a venv with all Python deps. Stage 2 is a slim runtime
# image with tesseract (for OCR) and the prepared venv copied over.
# The final image is around 400 MB — most of it is tesseract language
# data, which we keep because OCR accuracy depends on it.

# ---------- build ----------
FROM python:3.12-slim AS build

WORKDIR /build

# Build deps for any wheels that need compiling. We try to install
# everything as wheels; if a wheel isn't available we still need gcc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY shared ./shared
COPY runtime-engine ./runtime-engine
COPY plugins ./plugins
COPY packs ./packs

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        fastapi \
        'uvicorn[standard]' \
        pydantic \
        pydantic-settings \
        python-multipart \
        anthropic \
        pypdf \
        pyyaml \
        rapidocr-onnxruntime \
        pypdfium2

# ---------- runtime ----------
FROM python:3.12-slim

# RapidOCR + pypdfium2 cover the OCR path with no system deps. We no
# longer bundle tesseract by default — it's an opt-in for installs that
# need it (install at runtime with `apt install tesseract-ocr` and
# `pip install ocrmypdf`).

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY shared /app/shared
COPY runtime-engine /app/runtime-engine
COPY plugins /app/plugins
COPY packs /app/packs

# Non-root runtime user — limits blast radius if a PDF-parsing library
# is ever exploited. The user owns the app dir; the volume mounts
# inherit the host's ownership and need to be writable for UID 10001.
RUN groupadd -r -g 10001 sherpa \
    && useradd -r -u 10001 -g sherpa -d /app sherpa \
    && chown -R sherpa:sherpa /app
USER sherpa

# Mountable volumes for watch / classified / persistent data.
VOLUME ["/data", "/incoming", "/classified"]

ENV RUNTIME_ENGINE_WATCH_DIR=/incoming \
    RUNTIME_ENGINE_DESTINATION_ROOT=/classified \
    RUNTIME_ENGINE_DB_PATH=/data/runtime.db \
    RUNTIME_ENGINE_OCR_ENGINE=rapidocr \
    RUNTIME_ENGINE_WATCHER_PARALLELISM=2

EXPOSE 8002

CMD ["uvicorn", "runtime_engine.main:app", \
     "--app-dir", "/app/runtime-engine", \
     "--host", "0.0.0.0", "--port", "8002"]
