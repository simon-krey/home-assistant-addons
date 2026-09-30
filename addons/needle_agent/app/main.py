"""Einstiegspunkt: Wyoming-Conversation-Agent + Web-UI in einem Event-Loop."""

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

from . import logs
from .ha_client import HomeAssistantClient
from .history import ConversationHistory
from .matching import HomeContext
from .settings import DATA_DIR, load_settings
from .state import AppState
from .web import create_web_app
from .wyoming_handler import PROGRAM_NAME, HandleEventHandler

SUPERVISOR_DISCOVERY_URL = "http://supervisor/discovery"


def history_dir() -> Path:
    env = os.getenv("HISTORY_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/history")
    return Path(__file__).resolve().parent.parent / "data" / "history"


def _post_discovery(service: str, config: dict) -> tuple[bool, str]:
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        return False, "kein SUPERVISOR_TOKEN"
    body = json.dumps({"service": service, "config": config}).encode("utf-8")
    request = urllib.request.Request(
        SUPERVISOR_DISCOVERY_URL,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return True, f"HTTP {response.status}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


async def register_discovery(port: int) -> None:
    if not os.getenv("SUPERVISOR_TOKEN"):
        print(
            "[DISCOVERY] Kein SUPERVISOR_TOKEN (kein HAOS) – Wyoming ggf. manuell hinzufuegen",
            flush=True,
        )
        return
    uri = f"tcp://{socket.gethostname()}:{port}"
    for attempt in range(1, 6):
        ok, message = await asyncio.to_thread(_post_discovery, "wyoming", {"uri": uri})
        if ok:
            print(f"[DISCOVERY] Wyoming registriert: {uri} ({message})", flush=True)
            return
        print(f"[DISCOVERY] Versuch {attempt} fehlgeschlagen: {message}", flush=True)
        await asyncio.sleep(2)
    print("[DISCOVERY] Keine Registrierung – bitte Wyoming manuell hinzufuegen", flush=True)


async def load_home(
    ha: HomeAssistantClient,
    *,
    attempts: int = 5,
    base_delay: float = 2.0,
) -> tuple[list, HomeContext, str | None]:
    """Entities laden – mit Retry, weil HA direkt nach dem Boot oft noch nicht bereit ist."""
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            entities, context = await ha.fetch_home()
            if entities:
                return entities, context, None
            last_error = "Home Assistant lieferte 0 Entities (Registry/States leer)"
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
        print(f"[HA] Versuch {attempt}/{attempts} fehlgeschlagen: {last_error}", flush=True)
        if attempt < attempts:
            await asyncio.sleep(min(30.0, base_delay * attempt))
    return [], HomeContext(), last_error


async def refresh_loop(state: AppState) -> None:
    while True:
        # Solange keine Entities da sind, schneller nachladen (HA braucht evtl. noch Zeit)
        empty = not state.entities
        await asyncio.sleep(15.0 if empty else max(30, state.settings.refresh_seconds))
        try:
            entities, context = await state.ha.fetch_home()
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            with state.lock:
                state.stats["ha_error"] = message
            print(f"[HA] Refresh fehlgeschlagen: {message}", flush=True)
            continue
        with state.lock:
            old_names = sorted(e.name for e in state.entities)
            state.entities = entities
            state.context = context
            state.stats["refreshes"] += 1
            state.stats["ha_error"] = None if entities else "Home Assistant lieferte 0 Entities"
            changed = sorted(e.name for e in entities) != old_names
        if changed:
            state.reconfigure()
            print(f"[HA] Entities aktualisiert ({len(entities)}), Agent neu gebaut", flush=True)


async def initial_load(state: AppState) -> None:
    """HA verbinden und Entities laden – im Hintergrund, damit die UI sofort startet."""
    if not state.ha.connected:
        try:
            await state.ha.connect()
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {exc}"
            print(f"[HA ERROR] {message}", flush=True)
            with state.lock:
                state.stats["ha_error"] = message
            return
    entities, context, error = await load_home(state.ha)
    with state.lock:
        state.entities = entities
        state.context = context
        state.stats["ha_error"] = error
        state.stats["refreshes"] += 1
    state.reconfigure()
    print(f"[HA] Initial: {len(entities)} Entities geladen", flush=True)


async def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=logging.DEBUG if settings.debug_logging else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logs.install(
        logging.DEBUG if settings.debug_logging else logging.INFO,
        capture=settings.log_capture,
    )
    print(
        f"[APP] Needle Conversation – backend={settings.backend} domains={settings.domains} "
        f"dry_run={settings.dry_run}",
        flush=True,
    )

    history = ConversationHistory(history_dir(), limit=settings.history_limit)
    ha = HomeAssistantClient(settings, logger=print)

    state = AppState(
        settings=settings,
        ha=ha,
        history=history,
        tool_index_path=str(DATA_DIR / "needle" / "tools.idx"),
    )
    state.build()
    print(
        f"[APP] {len(state.current_toolset())} Tools, {len(state.current_toolset().entities)} Entities",
        flush=True,
    )

    app = create_web_app(state)
    server = AsyncServer.from_uri(f"tcp://0.0.0.0:{settings.wyoming_port}")
    await server.start(partial(HandleEventHandler, state))
    print(f"[WYOMING] tcp://0.0.0.0:{settings.wyoming_port} (program={PROGRAM_NAME})", flush=True)
    print(f"[WEBUI] http://0.0.0.0:{settings.web_port}", flush=True)

    asyncio.create_task(initial_load(state))
    asyncio.create_task(register_discovery(settings.wyoming_port))
    asyncio.create_task(refresh_loop(state))

    config = uvicorn.Config(app, host="0.0.0.0", port=settings.web_port, log_level="info")
    await uvicorn.Server(config).serve()


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
