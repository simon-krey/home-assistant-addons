"""FastAPI-Weboberflaeche zum Testen der Pipeline.

Bietet:
- Live-Event-Stream per WebSocket
- Texteingabe (ohne Mikrofon)
- Mikrofon Start/Stop
- Dry-Run-Umschalter
- Entity-/State-Uebersicht

Start:  python -m webui       (oder  python main.py --web)
"""

from __future__ import annotations

import asyncio
import queue
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import config
from events import EventBus
from pipeline import VoicePipeline

STATIC_DIR = Path(__file__).resolve().parent / "static"


class TextPayload(BaseModel):
    text: str


class BoolPayload(BaseModel):
    value: bool


def _get_or_none(channel: "queue.Queue[dict[str, Any]]", timeout: float) -> dict[str, Any] | None:
    try:
        return channel.get(timeout=timeout)
    except queue.Empty:
        return None


def create_app(pipeline: VoicePipeline | None = None) -> FastAPI:
    state: dict[str, Any] = {"pipeline": pipeline, "error": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["pipeline"] is None:
            try:
                built = VoicePipeline(
                    debug=True,
                    dry_run=config.HA_DRY_RUN,
                    bus=EventBus(),
                    logger=lambda message: print(message),
                    load_stt=True,
                )
                built.start()
                state["pipeline"] = built
                print("[WEBUI] Pipeline bereit")
            except Exception as exc:  # noqa: BLE001
                state["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[WEBUI ERROR] {state['error']}")
        yield
        if state["pipeline"] is not None:
            state["pipeline"].stop()

    app = FastAPI(title="Voice Assistant", lifespan=lifespan)

    def get_pipeline() -> VoicePipeline:
        if state["pipeline"] is None:
            raise HTTPException(status_code=503, detail=state["error"] or "Pipeline nicht bereit")
        return state["pipeline"]

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/api/state")
    async def api_state() -> dict[str, Any]:
        pipeline = get_pipeline()
        return {"snapshot": pipeline.snapshot(), "config": {
            "confidence_threshold": config.NEEDLE_CONFIDENCE_THRESHOLD,
            "sample_rate": config.SAMPLE_RATE,
        }}

    @app.post("/api/text")
    async def api_text(payload: TextPayload) -> dict[str, Any]:
        pipeline = get_pipeline()
        text = payload.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="Leerer Text")
        pipeline.submit_text(text)
        return {"ok": True, "text": text}

    @app.post("/api/mic")
    async def api_mic(payload: BoolPayload) -> dict[str, Any]:
        pipeline = get_pipeline()
        if payload.value:
            await asyncio.to_thread(pipeline.start_microphone)
        else:
            await asyncio.to_thread(pipeline.stop_microphone)
        return {"ok": True, "running": pipeline.microphone_running}

    @app.post("/api/dry-run")
    async def api_dry_run(payload: BoolPayload) -> dict[str, Any]:
        pipeline = get_pipeline()
        pipeline.set_dry_run(payload.value)
        return {"ok": True, "dry_run": pipeline.dry_run}

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        if state["pipeline"] is None:
            await websocket.send_json(
                {"type": "error", "source": "webui", "message": state["error"] or "nicht bereit"}
            )
            await websocket.close()
            return
        pipeline: VoicePipeline = state["pipeline"]
        channel = pipeline.bus.subscribe()
        try:
            await websocket.send_json({"type": "snapshot", "snapshot": pipeline.snapshot()})
            while True:
                event = await asyncio.to_thread(_get_or_none, channel, 0.5)
                if event is not None:
                    await websocket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            pipeline.bus.unsubscribe(channel)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=config.WEBUI_HOST, port=config.WEBUI_PORT, log_level="info")


if __name__ == "__main__":
    main()
