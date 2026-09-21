"""Web-UI (Ingress): Konfiguration, Status, Test, Verlauf, Diagnose."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from . import logs
from .backends import BACKENDS
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

    engine = state.current_engine()
    with state.lock:
        stats = dict(state.stats)
    if engine.backend.name != "needle":
        checks.append(
            {
                "name": "Needle-Backend",
                "ok": False,
                "detail": f"Fallback auf '{engine.backend.name}' aktiv – {stats.get('backend_error') or 'Ursache unbekannt'}",
            }
        )
    else:
        checks.append({"name": "Needle-Backend", "ok": True, "detail": "aktiv"})
    resolver = engine.resolver
    aliases = sum(len(e.aliases) for e in resolver.entities)
    areas = {e.area for e in resolver.entities if e.area}
    checks.append(
        {
            "name": "Resolver",
            "ok": len(resolver.entities) > 0,
            "detail": f"{len(resolver.entities)} Entities, {aliases} Aliase, {len(areas)} Areas",
        }
    )
    checks.append(
        {"name": "Fast-Path", "ok": settings.fast_path, "detail": "an" if settings.fast_path else "aus"}
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
        "backend": settings.backend,
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
                    "floor": e.floor,
                    "aliases": list(e.aliases),
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
            "backend": state.settings.backend,
            "effective_backend": state.current_engine().backend.name,
            "backend_error": stats.get("backend_error"),
            "log_capture": state.settings.log_capture,
            "last_error": stats.get("last_error"),
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
        return {
            "enabled": logs.is_enabled(),
            "entries": logs.LOG_BUFFER.entries(limit=max(1, min(400, limit))),
        }

    @app.post("/api/logs/clear")
    async def api_logs_clear() -> dict[str, Any]:
        logs.LOG_BUFFER.clear()
        return {"ok": True}

    @app.get("/api/diagnostics")
    async def api_diagnostics() -> dict[str, Any]:
        return _diagnostics(state)

    @app.get("/api/resolve")
    async def api_resolve(text: str) -> dict[str, Any]:
        engine = state.current_engine()
        resolver = engine.resolver
        candidates = resolver.resolve(text)
        command = engine.parser.parse(text)
        annotated, best = resolver.annotate(text)
        return {
            "text": text,
            "area": resolver.area_in(text),
            "floor": resolver.floor_in(text),
            "fast_path": state.settings.fast_path,
            "would_execute": command is not None and state.settings.fast_path,
            "candidates": [
                {
                    "entity_id": c.entity.entity_id,
                    "name": c.entity.name,
                    "area": c.entity.area,
                    "score": c.score,
                    "reason": c.reason,
                }
                for c in candidates[:8]
            ],
            "best": (
                {"entity_id": best.entity.entity_id, "name": best.entity.name, "score": best.score}
                if best
                else None
            ),
            "command": (
                {
                    "action": command.action,
                    "entity": command.entity.entity_id,
                    "value": command.value,
                    "reason": command.reason,
                }
                if command
                else None
            ),
            "annotated": annotated if annotated != text else None,
        }

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
            message = f"{type(exc).__name__}: {exc}"
            print(f"[BACKEND TEST] {message}\n{traceback.format_exc()}", flush=True)
            return {
                "ok": False,
                "backend": engine.backend.name,
                "error": message,
                "traceback": traceback.format_exc()[-2000:],
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
                settings.tools = (
                    ",".join(str(x) for x in raw) if isinstance(raw, list) else str(raw or "")
                )
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
            if "fallback_ha" in payload:
                settings.fallback_ha = bool(payload["fallback_ha"])
            if "ground_calls" in payload:
                settings.ground_calls = bool(payload["ground_calls"])
            if "debug_errors" in payload:
                settings.debug_errors = bool(payload["debug_errors"])
            if "fast_path" in payload:
                settings.fast_path = bool(payload["fast_path"])
            if "resolve_min_score" in payload:
                settings.resolve_min_score = max(0.0, min(1.0, float(payload["resolve_min_score"])))
            if "resolve_min_margin" in payload:
                settings.resolve_min_margin = max(0.0, min(1.0, float(payload["resolve_min_margin"])))
            if "resolve_floor" in payload:
                settings.resolve_floor = max(0.0, min(1.0, float(payload["resolve_floor"])))
            if "tool_match_min_score" in payload:
                settings.tool_match_min_score = max(
                    0.0, min(1.0, float(payload["tool_match_min_score"]))
                )
            if "low_confidence_threshold" in payload:
                settings.low_confidence_threshold = max(
                    0.0, min(1.0, float(payload["low_confidence_threshold"]))
                )
            if "needle_max_tokens" in payload:
                settings.needle_max_tokens = max(32, min(1024, int(payload["needle_max_tokens"])))
            if "log_capture" in payload:
                settings.log_capture = bool(payload["log_capture"])
        save_settings(settings)
        state.history.configure(settings.history_limit)
        logs.set_enabled(settings.log_capture)
        logs.set_level(logging.DEBUG if settings.debug_logging else logging.INFO)
        state.reconfigure()
        return status()

    @app.post("/api/settings/reset")
    async def api_reset_settings() -> dict[str, Any]:
        fresh = reset_to_addon_options()
        with state.lock:
            for field in (
                "dry_run", "domains", "tools", "max_steps", "language", "system",
                "refresh_seconds", "debug_logging", "fallback_ha", "ground_calls",
                "debug_errors", "fast_path", "resolve_min_score", "resolve_min_margin",
                "resolve_floor", "tool_match_min_score", "low_confidence_threshold",
                "needle_max_tokens", "log_capture",
            ):
                setattr(state.settings, field, getattr(fresh, field))
        logs.set_enabled(state.settings.log_capture)
        logs.set_level(logging.DEBUG if state.settings.debug_logging else logging.INFO)
        state.reconfigure()
        return status()

    @app.post("/api/refresh")
    async def api_refresh() -> dict[str, Any]:
        try:
            entities, context = await state.ha.fetch_home()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        with state.lock:
            state.entities = entities
            state.context = context
            state.stats["refreshes"] += 1
        state.reconfigure()
        return status()

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
