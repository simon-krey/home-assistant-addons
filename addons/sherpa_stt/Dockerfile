# Multi-Arch (amd64, arm64, arm/v7) – eigener Python-Base statt HA-Base,
# damit keine BUILD_FROM-Abhaengigkeit besteht.
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MODELS_DIR=/data/models \
    HISTORY_DIR=/data/history \
    WYOMING_PORT=10300 \
    WEB_PORT=8000 \
    STT_NUM_THREADS=2

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

VOLUME ["/data"]
EXPOSE 8000 10300

HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["python", "-m", "app.main"]
