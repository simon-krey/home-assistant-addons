"""Web-UI (Ingress): Konfiguration, Status, Test, Verlauf."""

from __future__ import annotations

import asyncio
import os
import platform
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from . import llama_models, logs
from .backends import BACKENDS, OPENAI_MODEL_CATALOG
from .responses import DEFAULT_TEMPLATES
from .settings import reset_to_addon_options, save_settings
from .state import AppState
from .tools import AVAILABLE_TOOLS, DEFAULT_TOOLS

try:
    import psutil
except Exception:  # noqa: BLE001
    psutil = None

STATIC_DIR = Path(__file__).resolve().parent / "static"


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


def _llama_info(state: AppState) -> dict[str, Any]:
    settings = state.settings
    spec = llama_models.resolve_model(settings.llama_model, settings.llama_repo, settings.llama_filename)
    path = llama_models.models_dir() / spec.filename
    try:
        loaded = bool(getattr(state.current_engine().backend, "loaded", False))
    except Exception:  # noqa: BLE001
        loaded = False
    return {
        "catalog": [
            {
                "id": model.key,
                "label": model.label,
                "repo": model.repo,
                "filename": model.filename,
                "approx_mb": model.approx_mb,
            }
            for model in llama_models.LLAMA_MODELS.values()
        ],
        "models_dir": str(llama_models.models_dir()),
        "model": spec.key,
        "repo": spec.repo,
        "filename": spec.filename,
        "path": str(path),
        "exists": path.exists(),
        "size_mb": round(path.stat().st_size / 1e6, 1) if path.exists() else None,
        "loaded": loaded,
        "download": llama_models.download_state(),
    }


def _diagnostics(state: AppState) -> dict[str, Any]:
    settings = state.settings
    checks: list[dict[str, Any]] = []

    checks.append(
        {
            "name": "Home Assistant",
            "ok": state.ha.connected,
            "detail": (
                f"mode={state.ha.mode}, version={state.ha.info.get('version')}"
                if state.ha.connected
                else "nicht verbunden"
            ),
        }
    )

    backend = (settings.backend or "needle").lower()
    if backend in ("llama_cpp", "llama", "llamacpp"):
        try:
            import llama_cpp

            checks.append(
                {"name": "llama-cpp-python", "ok": True, "detail": getattr(llama_cpp, "__version__", "?")}
            )
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {"name": "llama-cpp-python", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            )
        spec = llama_models.resolve_model(
            settings.llama_model, settings.llama_repo, settings.llama_filename
        )
        path = llama_models.models_dir() / spec.filename
        checks.append(
            {
                "name": "GGUF-Modell",
                "ok": path.exists(),
                "detail": f"{path} ({round(path.stat().st_size / 1e6, 1)} MB)"
                if path.exists()
                else f"fehlt: {path}",
            }
        )
    elif backend == "needle":
        try:
            import needle  # noqa: F401

            checks.append({"name": "cactus-needle", "ok": True, "detail": "importierbar"})
        except Exception as exc:  # noqa: BLE001
            checks.append(
                {"name": "cactus-needle", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}
            )
        cache = Path.home() / ".cache" / "cactus-needle"
        checks.append(
            {
                "name": "Needle-Engine/Weights",
                "ok": cache.exists(),
                "detail": str(cache) if cache.exists() else f"fehlt (erster Start braucht Internet): {cache}",
            }
        )
    elif backend == "openai":
        checks.append(
            {
                "name": "OpenAI-Endpunkt",
                "ok": bool(settings.openai_base_url),
                "detail": settings.openai_base_url or "leer",
            }
        )
    else:
        checks.append(
            {"name": "Backend 'ha'", "ok": state.ha.connected, "detail": "nutzt Home Assistant"}
        )

    toolset = state.current_toolset()
    checks.append(
        {"name": "Tools", "ok": len(toolset) > 0, "detail": ", ".join(toolset.names()) or "keine"}
    )
    checks.append(
        {
            "name": "Entities",
            "ok": len(toolset.entities) > 0,
            "detail": f"{len(toolset.entities)} (Domains: {settings.domains})",
        }
    )

    with state.lock:
        stats = dict(state.stats)
    return {
        "backend": backend,
        "checks": checks,
        "stats": stats,
        "system": {
            "machine": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
    }


def create_web_app(state: AppState) -> FastAPI:
    app = FastAPI(title="Needle 3 Conversation")

    def status() -> dict[str, Any]:
        toolset = state.current_toolset()
        with state.lock:
            stats = dict(state.stats)
        return {
            "settings": state.settings.to_dict(),
            "ha": {
                "connected": state.ha.connected,
                "mode": state.ha.mode,
                "message": state.ha.info.get("message"),
                "location_name": state.ha.info.get("location_name"),
                "version": state.ha.info.get("version"),
            },
            "tools": toolset.names(),
            "tool_count": len(toolset),
            "entity_count": len(toolset.entities),
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "name": e.name,
                    "domain": e.domain,
                    "area": e.area,
                    "state": state.ha.state(e.entity_id) or e.state,
                }
                for e in toolset.entities
            ],
            "stats": stats,
            "history_count": state.history.count(),
            "wyoming": {
                "uri": f"tcp://0.0.0.0:{state.settings.wyoming_port}",
                "program": "needle3",
            },
            "default_templates": DEFAULT_TEMPLATES,
            "available_tools": list(AVAILABLE_TOOLS),
            "default_tools": list(DEFAULT_TOOLS),
            "backends": list(BACKENDS),
            "model_catalog": OPENAI_MODEL_CATALOG,
            "backend": state.settings.backend,
            "llama": _llama_info(state),
            "last_error": stats.get("last_error"),
            "system": {
                "machine": platform.machine(),
                "python": platform.python_version(),
                "cpu_count": os.cpu_count(),
            },
        }

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": state.engine is not None, "ha": state.ha.connected}

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return status()

    @app.get("/api/metrics")
    async def api_metrics() -> dict[str, Any]:
        return _metrics()

    @app.get("/api/logs")
    async def api_logs(limit: int = 200) -> dict[str, Any]:
        return {"entries": logs.LOG_BUFFER.entries(limit=max(1, min(400, limit)))}

    @app.get("/api/diagnostics")
    async def api_diagnostics() -> dict[str, Any]:
        return _diagnostics(state)

    @app.post("/api/backend/test")
    async def api_backend_test() -> dict[str, Any]:
        engine = state.current_engine()
        started = time.perf_counter()
        try:
            decision = await engine.backend.begin(
                "Hallo", engine._system("Hallo"), engine.toolset.schemas()
            )
            return {
                "ok": True,
                "backend": engine.backend.name,
                "info": engine.backend.info(),
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "calls": [{"name": c.name, "arguments": c.arguments} for c in decision.calls],
                "text": decision.text,
            }
        except Exception as exc:  # noqa: BLE001
            import traceback as _tb

            message = f"{type(exc).__name__}: {exc}"
            print(f"[BACKEND TEST] {message}\n{_tb.format_exc()}", flush=True)
            return {
                "ok": False,
                "backend": engine.backend.name,
                "error": message,
                "traceback": _tb.format_exc()[-2000:],
            }

    @app.get("/api/settings")
    async def api_get_settings() -> dict[str, Any]:
        return state.settings.to_dict()

    @app.post("/api/settings")
    async def api_set_settings(payload: dict[str, Any]) -> dict[str, Any]:
        settings = state.settings
        with state.lock:
            if "dry_run" in payload:
                settings.dry_run = bool(payload["dry_run"])
            if "domains" in payload:
                settings.domains = str(payload["domains"] or "")
            if "tools" in payload:
                raw = payload["tools"]
                if isinstance(raw, list):
                    settings.tools = ",".join(str(x) for x in raw)
                else:
                    settings.tools = str(raw or "")
            if "max_steps" in payload:
                settings.max_steps = max(1, min(8, int(payload["max_steps"])))
            if "language" in payload:
                settings.language = str(payload["language"] or "de")
            if "system" in payload:
                settings.system = str(payload["system"] or "")
            if "refresh_seconds" in payload:
                settings.refresh_seconds = max(30, min(3600, int(payload["refresh_seconds"])))
            if "debug_logging" in payload:
                settings.debug_logging = bool(payload["debug_logging"])
            if "ha_url" in payload:
                settings.ha_url = str(payload["ha_url"] or "")
            if "ha_token" in payload:
                settings.ha_token = str(payload["ha_token"] or "")
            if "response_templates" in payload:
                settings.response_templates = str(payload["response_templates"] or "")
            if "history_limit" in payload:
                settings.history_limit = max(0, min(1000, int(payload["history_limit"])))
            if "backend" in payload:
                backend = str(payload["backend"] or "needle")
                if backend not in BACKENDS:
                    raise HTTPException(status_code=400, detail=f"unbekanntes Backend: {backend}")
                settings.backend = backend
            if "fallback_ha" in payload:
                settings.fallback_ha = bool(payload["fallback_ha"])
            if "ground_calls" in payload:
                settings.ground_calls = bool(payload["ground_calls"])
            if "debug_errors" in payload:
                settings.debug_errors = bool(payload["debug_errors"])
            if "needle_max_tokens" in payload:
                settings.needle_max_tokens = max(32, min(1024, int(payload["needle_max_tokens"])))
            if "openai_base_url" in payload:
                settings.openai_base_url = str(payload["openai_base_url"] or "")
            if "openai_api_key" in payload:
                settings.openai_api_key = str(payload["openai_api_key"] or "")
            if "openai_model" in payload:
                settings.openai_model = str(payload["openai_model"] or "")
            if "openai_temperature" in payload:
                settings.openai_temperature = max(0.0, min(2.0, float(payload["openai_temperature"])))
            if "openai_max_tokens" in payload:
                settings.openai_max_tokens = max(32, min(4096, int(payload["openai_max_tokens"])))
            if "llama_model" in payload:
                settings.llama_model = str(payload["llama_model"] or "")
            if "llama_repo" in payload:
                settings.llama_repo = str(payload["llama_repo"] or "")
            if "llama_filename" in payload:
                settings.llama_filename = str(payload["llama_filename"] or "")
            if "llama_n_ctx" in payload:
                settings.llama_n_ctx = max(512, min(32768, int(payload["llama_n_ctx"])))
            if "llama_threads" in payload:
                settings.llama_threads = max(0, min(32, int(payload["llama_threads"])))
            if "llama_gpu_layers" in payload:
                settings.llama_gpu_layers = max(0, min(200, int(payload["llama_gpu_layers"])))
            if "llama_temperature" in payload:
                settings.llama_temperature = max(0.0, min(2.0, float(payload["llama_temperature"])))
            if "llama_max_tokens" in payload:
                settings.llama_max_tokens = max(32, min(4096, int(payload["llama_max_tokens"])))
            if "llama_disable_thinking" in payload:
                settings.llama_disable_thinking = bool(payload["llama_disable_thinking"])
        save_settings(settings)
        state.history.configure(settings.history_limit)
        state.reconfigure()
        return status()

    @app.post("/api/settings/reset")
    async def api_reset_settings() -> dict[str, Any]:
        fresh = reset_to_addon_options()
        with state.lock:
            for field in (
                "backend", "dry_run", "domains", "tools", "max_steps", "language",
                "system", "refresh_seconds", "debug_logging", "fallback_ha",
                "ground_calls", "debug_errors", "needle_max_tokens", "openai_base_url", "openai_model",
                "openai_temperature", "openai_max_tokens",
                "llama_model", "llama_repo", "llama_filename", "llama_n_ctx",
                "llama_threads", "llama_gpu_layers", "llama_temperature",
                "llama_max_tokens", "llama_disable_thinking",
            ):
                setattr(state.settings, field, getattr(fresh, field))
        state.reconfigure()
        return status()

    @app.post("/api/refresh")
    async def api_refresh() -> dict[str, Any]:
        try:
            entities = await state.ha.fetch_entities()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        with state.lock:
            state.entities = entities
            state.stats["refreshes"] += 1
        state.reconfigure()
        return status()

    @app.post("/api/llama/download")
    async def api_llama_download() -> dict[str, Any]:
        settings = state.settings
        spec = llama_models.resolve_model(
            settings.llama_model, settings.llama_repo, settings.llama_filename
        )
        if llama_models.download_state().get("active"):
            raise HTTPException(status_code=409, detail="Download laeuft bereits")

        async def _run() -> None:
            try:
                await asyncio.to_thread(llama_models.ensure_model, spec, None, print)
            except Exception as exc:  # noqa: BLE001
                print(f"[LLAMA] Download fehlgeschlagen: {exc}", flush=True)

        asyncio.create_task(_run())
        return {"ok": True, "model": spec.key, "repo": spec.repo, "filename": spec.filename}

    @app.post("/api/test")
    async def api_test(payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="leerer Text")
        result = await state.current_engine().process(text, state.settings.language)
        with state.lock:
            state.stats["last_error"] = result.get("error_message")
        return result

    @app.get("/api/history")
    async def api_history(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        return {
            "count": state.history.count(),
            "entries": state.history.list(limit=max(1, min(500, limit)), offset=max(0, offset)),
        }

    @app.delete("/api/history")
    async def api_history_clear() -> dict[str, Any]:
        state.history.clear()
        return {"ok": True, "count": 0}

    return app
