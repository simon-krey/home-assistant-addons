"""Web-UI (Ingress) fuer Konfiguration, Status und Verlauf."""

from __future__ import annotations

import asyncio
import os
import platform
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

from .engine import MODELS, build_engine
from .settings import reset_to_addon_options, save_settings
from .state import AppState

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

STATIC_DIR = Path(__file__).resolve().parent / "static"

EDITABLE = {
    "model",
    "model_url",
    "model_type",
    "kind",
    "language",
    "num_threads",
    "streaming_transcripts",
    "save_audio",
    "history_limit",
    "zeroconf",
    "debug_logging",
}


def _metrics() -> dict[str, Any]:
    data: dict[str, Any] = {}
    if psutil is not None:
        data["cpu_percent"] = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        data["ram_percent"] = vm.percent
        data["ram_used_mb"] = round(vm.used / 1e6, 1)
        data["ram_total_mb"] = round(vm.total / 1e6, 1)
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                first = next(iter(temps.values()))
                if first:
                    data["cpu_temp_c"] = first[0].current
        except Exception:  # noqa: BLE001
            pass
    try:
        data["loadavg"] = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        pass
    return data


def create_web_app(state: AppState) -> FastAPI:
    app = FastAPI(title="Sherpa STT (Wyoming)")

    def status() -> dict[str, Any]:
        settings = state.settings
        with state.lock:
            stats = dict(state.stats)
        return {
            "engine": state.current_engine().info(),
            "settings": settings.to_dict(),
            "stats": stats,
            "history_count": state.history.count(),
            "wyoming": {
                "uri": f"tcp://0.0.0.0:{settings.wyoming_port}",
                "program": "sherpa-onnx",
            },
            "system": {
                "machine": platform.machine(),
                "python": platform.python_version(),
                "cpu_count": os.cpu_count(),
            },
            "models": {key: spec.label for key, spec in MODELS.items()},
        }

    def build_configured_engine(settings) -> Any:  # noqa: ANN001
        return build_engine(
            settings.model,
            settings.model_url or None,
            settings.num_threads,
            settings.model_type or None,
            settings.languages() or None,
            settings.kind or None,
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "engine": state.current_engine().model_key}

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return status()

    @app.get("/api/metrics")
    async def api_metrics() -> dict[str, Any]:
        return _metrics()

    @app.get("/api/settings")
    async def api_get_settings() -> dict[str, Any]:
        return state.settings.to_dict()

    @app.post("/api/settings")
    async def api_set_settings(payload: dict[str, Any]) -> dict[str, Any]:
        settings = state.settings
        with state.lock:
            previous = (
                settings.model,
                settings.model_url,
                settings.model_type,
                settings.kind,
                settings.num_threads,
                settings.language,
            )

            if "model" in payload:
                model = str(payload["model"])
                if model not in MODELS:
                    raise HTTPException(status_code=400, detail=f"unbekanntes Modell: {model}")
                settings.model = model
            if "model_url" in payload:
                settings.model_url = str(payload["model_url"] or "")
            if "model_type" in payload:
                settings.model_type = str(payload["model_type"] or "")
            if "kind" in payload:
                kind = str(payload["kind"] or "")
                if kind not in ("", "streaming", "whisper", "canary"):
                    raise HTTPException(status_code=400, detail=f"unbekannte Art: {kind}")
                settings.kind = kind
            if "language" in payload:
                settings.language = str(payload["language"] or "")
            if "num_threads" in payload:
                settings.num_threads = max(1, min(8, int(payload["num_threads"])))
            if "streaming_transcripts" in payload:
                settings.streaming_transcripts = bool(payload["streaming_transcripts"])
            if "save_audio" in payload:
                settings.save_audio = bool(payload["save_audio"])
            if "history_limit" in payload:
                settings.history_limit = max(0, min(1000, int(payload["history_limit"])))
            if "zeroconf" in payload:
                settings.zeroconf = str(payload["zeroconf"] or "")
            if "debug_logging" in payload:
                settings.debug_logging = bool(payload["debug_logging"])

        save_settings(settings)
        state.history.configure(settings.history_limit, settings.save_audio)

        if previous != (
            settings.model,
            settings.model_url,
            settings.model_type,
            settings.kind,
            settings.num_threads,
            settings.language,
        ):
            engine = await asyncio.to_thread(build_configured_engine, settings)
            state.replace_engine(engine)
        return status()

    @app.post("/api/settings/reset")
    async def api_reset_settings() -> dict[str, Any]:
        settings = reset_to_addon_options()
        with state.lock:
            state.settings.model = settings.model
            state.settings.model_url = settings.model_url
            state.settings.model_type = settings.model_type
            state.settings.kind = settings.kind
            state.settings.language = settings.language
            state.settings.num_threads = settings.num_threads
            state.settings.streaming_transcripts = settings.streaming_transcripts
            state.settings.save_audio = settings.save_audio
            state.settings.history_limit = settings.history_limit
            state.settings.zeroconf = settings.zeroconf
            state.settings.debug_logging = settings.debug_logging
        state.history.configure(state.settings.history_limit, state.settings.save_audio)
        engine = await asyncio.to_thread(build_configured_engine, state.settings)
        state.replace_engine(engine)
        return status()

    @app.get("/api/history")
    async def api_history(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return {
            "count": state.history.count(),
            "entries": state.history.list(limit=max(1, min(500, limit)), offset=max(0, offset)),
        }

    @app.get("/api/history/{entry_id}/audio")
    async def api_history_audio(entry_id: str) -> FileResponse:
        path = state.history.audio_path(entry_id)
        if path is None:
            raise HTTPException(status_code=404, detail="kein Audio vorhanden")
        return FileResponse(path, media_type="audio/wav", filename=f"{entry_id}.wav")

    @app.delete("/api/history/{entry_id}")
    async def api_history_delete(entry_id: str) -> dict[str, Any]:
        deleted = state.history.delete(entry_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
        return {"ok": True, "count": state.history.count()}

    @app.delete("/api/history")
    async def api_history_clear() -> dict[str, Any]:
        state.history.clear()
        return {"ok": True, "count": 0}

    return app
