"""Einstiegspunkt: Wyoming-ASR-Server + Web-UI in einem Event-Loop."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import urllib.request
from functools import partial
from pathlib import Path

import uvicorn
from wyoming.server import AsyncServer, AsyncTcpServer

from .engine import build_engine
from .history import HistoryStore
from .settings import load_settings
from .state import AppState
from .web import create_web_app
from .wyoming_handler import PROGRAM_NAME, SttEventHandler

SUPERVISOR_DISCOVERY_URL = "http://supervisor/discovery"


def history_dir() -> Path:
    env = os.getenv("HISTORY_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/history")
    return Path(__file__).resolve().parent.parent / "data" / "history"


def _post_discovery(service: str, config: dict) -> tuple[bool, str]:
    """Discovery an den Supervisor melden (nur unter HAOS moeglich)."""
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        return False, "kein SUPERVISOR_TOKEN (kein HAOS)"
    body = json.dumps({"service": service, "config": config}).encode("utf-8")
    request = urllib.request.Request(
        SUPERVISOR_DISCOVERY_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return True, f"HTTP {response.status}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def register_discovery(port: int) -> None:
    """Add-on als Wyoming-Dienst anmelden -> HA findet es automatisch."""
    if not os.getenv("SUPERVISOR_TOKEN"):
        print(
            "[DISCOVERY] Kein SUPERVISOR_TOKEN (kein HAOS) – "
            "Wyoming ggf. manuell hinzufuegen",
            flush=True,
        )
        return
    hostname = socket.gethostname()
    uri = f"tcp://{hostname}:{port}"
    for attempt in range(1, 6):
        ok, message = await asyncio.to_thread(_post_discovery, "wyoming", {"uri": uri})
        if ok:
            print(f"[DISCOVERY] Wyoming registriert: {uri} ({message})", flush=True)
            return
        print(f"[DISCOVERY] Versuch {attempt} fehlgeschlagen: {message}", flush=True)
        await asyncio.sleep(2)
    print("[DISCOVERY] Keine Registrierung – bitte Wyoming manuell hinzufuegen", flush=True)


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug_logging else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(
        f"[APP] Modell={settings.model} threads={settings.num_threads} "
        f"language={settings.language or '(aus Modell)'}",
        flush=True,
    )

    engine = await asyncio.to_thread(
        build_engine, settings.model, settings.model_url or None, settings.num_threads
    )
    history = HistoryStore(
        history_dir(), limit=settings.history_limit, save_audio=settings.save_audio
    )
    state = AppState(settings=settings, engine=engine, history=history)
    app = create_web_app(state)

    server = AsyncServer.from_uri(f"tcp://0.0.0.0:{settings.wyoming_port}")
    if settings.zeroconf and isinstance(server, AsyncTcpServer):
        try:
            from wyoming.zeroconf import HomeAssistantZeroconf

            await HomeAssistantZeroconf(
                name=settings.zeroconf, port=server.port, host=server.host
            ).register_server()
            print(f"[ZEROCONF] {settings.zeroconf}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[ZEROCONF ERROR] {exc}", flush=True)

    await server.start(partial(SttEventHandler, state))
    print(
        f"[WYOMING] tcp://0.0.0.0:{settings.wyoming_port} (program={PROGRAM_NAME})",
        flush=True,
    )
    print(f"[WEBUI] http://0.0.0.0:{settings.web_port}", flush=True)

    # HAOS: beim Supervisor als Wyoming-Dienst anmelden (Discovery).
    asyncio.create_task(register_discovery(settings.wyoming_port))

    config = uvicorn.Config(
        app, host="0.0.0.0", port=settings.web_port, log_level="info"
    )
    await uvicorn.Server(config).serve()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
