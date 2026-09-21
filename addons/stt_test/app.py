"""Realtime-STT-Test fuer Raspberry Pi / HAOS.

Browser-Mikrofon -> WebSocket -> sherpa-onnx Streaming-Zipformer -> UI.

Zusaetzlich: Datei-Benchmark (RTF) und Echtzeit-Simulation ohne Mikrofon.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import stt_engine
from stt_engine import STTEngine, benchmark, ensure_model, read_wav, resample

STATIC_DIR = Path(__file__).resolve().parent / "static"

try:  # psutil ist optional
    import psutil
except Exception:  # noqa: BLE001
    psutil = None


def load_addon_options() -> dict[str, Any]:
    path = Path("/data/options.json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _system_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "machine": platform.machine(),
        "system": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
    }
    try:
        info["loadavg"] = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        info["loadavg"] = None
    if psutil is not None:
        vm = psutil.virtual_memory()
        info["ram_total_mb"] = round(vm.total / 1e6, 1)
    else:
        info["ram_total_mb"] = None
    return info


def _metrics() -> dict[str, Any]:
    data: dict[str, Any] = {}
    if psutil is not None:
        data["cpu_percent"] = psutil.cpu_percent(interval=None)
        data["cpu_per_core"] = psutil.cpu_percent(interval=None, percpu=True)
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


def build_engine(model_key: str, model_url: str | None) -> STTEngine:
    spec = stt_engine.MODELS.get(model_key, stt_engine.MODELS["de"])
    model_dir = ensure_model(model_key, model_url=model_url)
    return STTEngine(
        model_dir,
        model_type=spec.get("model_type", ""),
        label=spec["label"],
    )


def create_app(engine: STTEngine | None = None) -> FastAPI:
    state: dict[str, Any] = {"engine": engine, "error": None, "model_key": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["engine"] is None:
            options = load_addon_options()
            model_key = os.getenv("STT_MODEL") or options.get("model") or "de"
            model_url = os.getenv("STT_MODEL_URL") or options.get("model_url") or None
            state["model_key"] = model_key
            try:
                state["engine"] = await asyncio.to_thread(build_engine, model_key, model_url)
                print("[APP] Engine bereit", flush=True)
            except Exception as exc:  # noqa: BLE001
                state["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[APP ERROR] {state['error']}", flush=True)
        yield

    app = FastAPI(title="Pi STT Realtime Test", lifespan=lifespan)

    def get_engine() -> STTEngine:
        if state["engine"] is None:
            raise HTTPException(status_code=503, detail=state["error"] or "Engine nicht bereit")
        return state["engine"]

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": state["engine"] is not None, "error": state["error"]}

    @app.get("/api/info")
    async def api_info() -> dict[str, Any]:
        engine = state["engine"]
        return {
            "engine": engine.info.__dict__ if engine is not None else None,
            "system": _system_info(),
            "models": {k: v["label"] for k, v in stt_engine.MODELS.items()},
            "model_key": state["model_key"],
            "error": state["error"],
        }

    @app.get("/api/metrics")
    async def api_metrics() -> dict[str, Any]:
        return _metrics()

    @app.post("/api/model")
    async def api_model(payload: dict[str, Any]) -> dict[str, Any]:
        key = payload.get("model")
        if key not in stt_engine.MODELS:
            raise HTTPException(status_code=400, detail=f"unbekanntes Modell: {key}")
        engine = await asyncio.to_thread(build_engine, key, None)
        state["engine"] = engine
        state["model_key"] = key
        state["error"] = None
        return {"ok": True, "engine": engine.info.__dict__}

    @app.get("/api/selftest")
    async def api_selftest() -> dict[str, Any]:
        engine = get_engine()
        wavs = sorted((engine.model_dir / "test_wavs").glob("*.wav"))
        if not wavs:
            raise HTTPException(status_code=404, detail="keine Test-WAV im Modell")
        samples, rate = read_wav(wavs[0].read_bytes())
        samples = resample(samples, rate, engine.sample_rate)
        return await asyncio.to_thread(benchmark, engine, samples)

    @app.post("/api/benchmark")
    async def api_benchmark(
        file: UploadFile = File(...),
        realtime: bool = False,
    ) -> dict[str, Any]:
        engine = get_engine()
        data = await file.read()
        try:
            samples, rate = read_wav(data)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        samples = resample(samples, rate, engine.sample_rate)
        result = await asyncio.to_thread(benchmark, engine, samples, realtime=realtime)
        result["filename"] = file.filename
        return result

    @app.websocket("/ws/stt")
    async def ws_stt(websocket: WebSocket) -> None:
        await websocket.accept()
        if state["engine"] is None:
            await websocket.send_json(
                {"type": "error", "message": state["error"] or "Engine nicht bereit"}
            )
            await websocket.close()
            return

        engine: STTEngine = state["engine"]
        session = engine.create_session()
        audio_samples = 0
        process_seconds = 0.0
        chunks = 0
        partials = 0
        last_text = ""
        last_stats = time.time()
        started = time.perf_counter()

        def step(samples: np.ndarray) -> tuple[str, bool, float]:
            t0 = time.perf_counter()
            session.accept(samples)
            text = session.result()
            endpoint = session.endpoint()
            return text, endpoint, time.perf_counter() - t0

        await websocket.send_json(
            {"type": "ready", "engine": engine.info.__dict__, "sample_rate": engine.sample_rate}
        )

        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    break

                raw = message.get("bytes")
                text_message = message.get("text")

                if raw:
                    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                    audio_samples += len(samples)
                    chunks += 1
                    text, endpoint, decode_seconds = await asyncio.to_thread(step, samples)
                    process_seconds += decode_seconds

                    if text != last_text:
                        last_text = text
                        if text:
                            partials += 1
                            await websocket.send_json(
                                {
                                    "type": "partial",
                                    "text": text,
                                    "decode_ms": round(decode_seconds * 1000, 1),
                                }
                            )

                    if endpoint:
                        final = (text or "").strip()
                        session.reset()
                        last_text = ""
                        audio_seconds = audio_samples / engine.sample_rate
                        await websocket.send_json(
                            {
                                "type": "final",
                                "text": final,
                                "audio_seconds": round(audio_seconds, 2),
                                "rtf": round(process_seconds / audio_seconds, 4)
                                if audio_seconds
                                else 0,
                            }
                        )

                    now = time.time()
                    if now - last_stats >= 1.0:
                        last_stats = now
                        audio_seconds = audio_samples / engine.sample_rate
                        wall_seconds = time.perf_counter() - started
                        await websocket.send_json(
                            {
                                "type": "stats",
                                "audio_seconds": round(audio_seconds, 2),
                                "process_seconds": round(process_seconds, 3),
                                "wall_seconds": round(wall_seconds, 2),
                                "rtf": round(process_seconds / audio_seconds, 4)
                                if audio_seconds
                                else 0,
                                "chunks": chunks,
                                "partials": partials,
                                "last_decode_ms": round(decode_seconds * 1000, 1),
                                "realtime": process_seconds / audio_seconds < 1.0
                                if audio_seconds
                                else True,
                            }
                        )
                elif text_message:
                    try:
                        command = json.loads(text_message)
                    except json.JSONDecodeError:
                        continue
                    if command.get("type") == "stop":
                        final = await asyncio.to_thread(session.finalize)
                        audio_seconds = audio_samples / engine.sample_rate
                        await websocket.send_json(
                            {
                                "type": "final",
                                "text": final.strip(),
                                "audio_seconds": round(audio_seconds, 2),
                                "rtf": round(process_seconds / audio_seconds, 4)
                                if audio_seconds
                                else 0,
                            }
                        )
                        break
        except WebSocketDisconnect:
            pass

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
