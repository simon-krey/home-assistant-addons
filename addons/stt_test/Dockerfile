# Multi-Arch-faehig (amd64, arm64, arm/v7) fuer HAOS-Add-on und Docker.
FROM python:3.12-slim-bookworm

# HA-Supervisor uebergibt diese Build-Args; hier nur deklariert, damit keine Warnungen entstehen.
ARG BUILD_ARCH
ARG BUILD_VERSION
ARG BUILD_FROM

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MODELS_DIR=/data/models \
    HOST=0.0.0.0 \
    PORT=8000 \
    STT_NUM_THREADS=2

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=240s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
