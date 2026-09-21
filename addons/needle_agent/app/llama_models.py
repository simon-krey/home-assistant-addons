"""GGUF-Modelle fuer den eingebauten llama.cpp-Backend.

Modelle werden bei Bedarf automatisch von Hugging Face nach
``/data/models/llama`` geladen. Der Download-Status ist global abfragbar,
damit die Web-UI Fortschritt anzeigen kann.
"""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

LogFn = Callable[[str], None]


@dataclass(frozen=True)
class LlamaModel:
    key: str
    label: str
    repo: str
    filename: str
    approx_mb: int = 0


LLAMA_MODELS: dict[str, LlamaModel] = {
    "qwen2.5-1.5b": LlamaModel(
        "qwen2.5-1.5b", "Qwen2.5 1.5B Instruct – empfohlen (~1.0 GB)",
        "Qwen/Qwen2.5-1.5B-Instruct-GGUF", "qwen2.5-1.5b-instruct-q4_k_m.gguf", 1000,
    ),
    "qwen3-0.6b": LlamaModel(
        "qwen3-0.6b", "Qwen3 0.6B – Pi-freundlich (~0.4 GB)",
        "unsloth/Qwen3-0.6B-GGUF", "Qwen3-0.6B-Q4_K_M.gguf", 400,
    ),
    "qwen3-1.7b": LlamaModel(
        "qwen3-1.7b", "Qwen3 1.7B (~1.1 GB, 'denkt' ggf. nach)",
        "unsloth/Qwen3-1.7B-GGUF", "Qwen3-1.7B-Q4_K_M.gguf", 1100,
    ),
    "llama-3.2-1b": LlamaModel(
        "llama-3.2-1b", "Llama 3.2 1B Instruct (~0.8 GB)",
        "bartowski/Llama-3.2-1B-Instruct-GGUF", "Llama-3.2-1B-Instruct-Q4_K_M.gguf", 800,
    ),
    "llama-3.2-3b": LlamaModel(
        "llama-3.2-3b", "Llama 3.2 3B Instruct (~2.0 GB)",
        "bartowski/Llama-3.2-3B-Instruct-GGUF", "Llama-3.2-3B-Instruct-Q4_K_M.gguf", 2000,
    ),
    "phi-4-mini": LlamaModel(
        "phi-4-mini", "Phi-4 mini Instruct 3.8B (~2.5 GB)",
        "unsloth/Phi-4-mini-instruct-GGUF", "Phi-4-mini-instruct-Q4_K_M.gguf", 2500,
    ),
    "qwen3-4b": LlamaModel(
        "qwen3-4b", "Qwen3 4B Instruct 2507 (~2.5 GB, beste Qualität)",
        "unsloth/Qwen3-4B-Instruct-2507-GGUF", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf", 2500,
    ),
    "gemma-3-1b": LlamaModel(
        "gemma-3-1b", "Gemma 3 1B IT (~0.8 GB, nur Chat, kein Tool-Calling)",
        "ggml-org/gemma-3-1b-it-GGUF", "gemma-3-1b-it-Q4_K_M.gguf", 800,
    ),
}

DEFAULT_LLAMA_MODEL = "qwen2.5-1.5b"

_state_lock = threading.Lock()
DOWNLOAD_STATE: dict[str, Any] = {
    "active": False,
    "model": None,
    "filename": None,
    "downloaded_mb": 0.0,
    "total_mb": 0.0,
    "percent": 0.0,
    "error": None,
    "done": False,
    "updated_at": None,
}


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        DOWNLOAD_STATE.update(kwargs)
        DOWNLOAD_STATE["updated_at"] = time.time()


def download_state() -> dict[str, Any]:
    with _state_lock:
        return dict(DOWNLOAD_STATE)


def models_dir() -> Path:
    env = os.getenv("LLAMA_MODELS_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/models/llama")
    return Path(__file__).resolve().parent.parent / "models" / "llama"


def resolve_model(model_key: str, repo: str = "", filename: str = "") -> LlamaModel:
    """Preset oder eigenes Repo/Datei-Paar."""
    if repo and filename:
        return LlamaModel("custom", f"{repo}/{filename}", repo, filename)
    return LLAMA_MODELS.get(model_key, LLAMA_MODELS[DEFAULT_LLAMA_MODEL])


def model_path(spec: LlamaModel, directory: str | Path | None = None) -> Path:
    return Path(directory) if directory else models_dir() / spec.filename


def _download(url: str, dest: Path, *, spec: LlamaModel, logger: LogFn) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    token = os.getenv("HF_TOKEN") or os.getenv("HUGGING_FACE_HUB_TOKEN")
    headers = {"User-Agent": "voice-assistant-addon"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    _set_state(active=True, model=spec.key, filename=spec.filename, downloaded_mb=0.0,
               total_mb=float(spec.approx_mb), percent=0.0, error=None, done=False)
    logger(f"[LLAMA] Download: {url}")
    last_report = 0.0
    done = 0
    with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0) or (spec.approx_mb * 1_000_000)
        while True:
            chunk = response.read(512 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last_report > 2:
                last_report = now
                percent = (done / total * 100) if total else 0.0
                _set_state(downloaded_mb=round(done / 1e6, 1), total_mb=round(total / 1e6, 1),
                           percent=round(percent, 1))
                logger(f"[LLAMA]   {done / 1e6:.1f}/{total / 1e6:.1f} MB ({percent:.0f}%)")
    tmp.rename(dest)


def ensure_model(spec: LlamaModel, directory: str | Path | None = None, logger: LogFn = print) -> Path:
    """GGUF sicherstellen; bei Bedarf von Hugging Face laden."""
    target = Path(directory) / spec.filename if directory else models_dir() / spec.filename
    if target.exists() and target.stat().st_size > 0:
        return target
    url = f"https://huggingface.co/{spec.repo}/resolve/main/{spec.filename}?download=true"
    try:
        _download(url, target, spec=spec, logger=logger)
    except urllib.error.HTTPError as exc:
        _set_state(active=False, error=f"HTTP {exc.code}: {spec.repo}/{spec.filename}")
        raise
    except Exception as exc:  # noqa: BLE001
        _set_state(active=False, error=f"{type(exc).__name__}: {exc}")
        raise
    size_mb = round(target.stat().st_size / 1e6, 1)
    _set_state(active=False, done=True, downloaded_mb=size_mb, total_mb=size_mb, percent=100.0, error=None)
    logger(f"[LLAMA] Fertig: {target.name} ({size_mb} MB)")
    return target
