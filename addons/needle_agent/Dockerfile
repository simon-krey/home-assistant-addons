# Multi-Arch (amd64, arm64, arm/v7). Eigener Python-Base, kein BUILD_FROM.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NEEDLE_TELEMETRY=0 \
    DO_NOT_TRACK=1 \
    WYOMING_PORT=10300 \
    WEB_PORT=8000 \
    LLAMA_MODELS_DIR=/data/models/llama

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Eingebauter llama.cpp-Backend (GGUF, CPU). Aus Quelle bauen; danach werden
# die Build-Tools wieder entfernt, libgomp1 (OpenMP) bleibt.
# GGML_NATIVE=OFF -> portable Binaerdatei, LLAMA_CURL=OFF -> kein libcurl noetig.
COPY requirements-llama.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential cmake \
    && CMAKE_ARGS="-DGGML_NATIVE=OFF -DLLAMA_CURL=OFF" \
       pip install --no-cache-dir -r requirements-llama.txt \
    && apt-get purge -y --auto-remove build-essential cmake \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app

VOLUME ["/data"]
EXPOSE 8000 10300

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["python", "-m", "app.main"]
