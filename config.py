"""Zentrale Konfiguration.

Alle Werte lassen sich per Umgebungsvariable oder ``.env`` überschreiben.
Die Modulpfade stehen bewusst nicht fest im Code.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Telemetrie von Needle/Cactus abschalten, bevor ``needle`` importiert wird.
os.environ.setdefault("NEEDLE_TELEMETRY", "0")
os.environ.setdefault("DO_NOT_TRACK", "1")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _opt_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser() if raw else default


# --- Audio ---------------------------------------------------------------
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
CHANNELS = int(os.getenv("CHANNELS", "1"))
AUDIO_BLOCK_MS = int(os.getenv("AUDIO_BLOCK_MS", "100"))
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE") or None

# --- STT -----------------------------------------------------------------
DEFAULT_STT_MODEL_DIR = (
    BASE_DIR / "models" / "sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06"
)
STT_MODEL_DIR = _opt_path("STT_MODEL_DIR", DEFAULT_STT_MODEL_DIR)
STT_NUM_THREADS = int(os.getenv("STT_NUM_THREADS", "2"))
STT_PROVIDER = os.getenv("STT_PROVIDER", "cpu")
STT_ENABLE_ENDPOINT = _bool("STT_ENABLE_ENDPOINT", True)
STT_RULE1_TRAILING_SILENCE = float(os.getenv("STT_RULE1_TRAILING_SILENCE", "2.4"))
STT_RULE2_TRAILING_SILENCE = float(os.getenv("STT_RULE2_TRAILING_SILENCE", "1.0"))
STT_RULE3_MIN_UTTERANCE_LENGTH = float(
    os.getenv("STT_RULE3_MIN_UTTERANCE_LENGTH", "20.0")
)
# "zipformer2" fuer die neuen Kroko-Modelle; leer = sherpa-onnx entscheidet.
STT_MODEL_TYPE = os.getenv("STT_MODEL_TYPE", "zipformer2")

# --- Needle --------------------------------------------------------------
NEEDLE_MAX_NEW_TOKENS = int(os.getenv("NEEDLE_MAX_NEW_TOKENS", "256"))
NEEDLE_CONFIDENCE_THRESHOLD = float(os.getenv("NEEDLE_CONFIDENCE_THRESHOLD", "0.7"))
NEEDLE_TOOL_INDEX_PATH = _opt_path(
    "NEEDLE_TOOL_INDEX_PATH", BASE_DIR / "models" / "needle" / "tools.idx"
)
NEEDLE_SYSTEM = os.getenv("NEEDLE_SYSTEM") or None

# --- Home Assistant ------------------------------------------------------
HA_USE_MOCK = _bool("HA_USE_MOCK", True)
HA_DRY_RUN = _bool("HA_DRY_RUN", True)
HA_URL = os.getenv("HA_URL", "")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_RECONNECT = _bool("HA_RECONNECT", True)
HA_RECONNECT_DELAY = float(os.getenv("HA_RECONNECT_DELAY", "3.0"))

# --- Web UI --------------------------------------------------------------
WEBUI_HOST = os.getenv("WEBUI_HOST", "127.0.0.1")
WEBUI_PORT = int(os.getenv("WEBUI_PORT", "8000"))
