"""Minimaler Home-Assistant-Zugriff, um Entity-Namen als Hotwords zu laden.

Im Add-on laeuft das ueber den Supervisor-Proxy (``homeassistant_api: true``),
ausserhalb ueber ``ha_url`` + ``ha_token``. Der Zugriff ist rein lesend und
schlaegt leise fehl, wenn HA nicht erreichbar ist – STT funktioniert dann
einfach ohne automatische Hotwords weiter.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class HomeAssistantSource:
    def __init__(self, ha_url: str = "", ha_token: str = "", logger=print) -> None:
        self.ha_url = (ha_url or "").rstrip("/")
        self.ha_token = ha_token or ""
        self.logger = logger

    def endpoints(self) -> dict[str, str]:
        token = os.getenv("SUPERVISOR_TOKEN")
        if token:
            return {
                "api": "http://supervisor/core/api",
                "ws": "ws://supervisor/core/websocket",
                "token": token,
                "mode": "supervisor",
            }
        ws = self.ha_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return {
            "api": f"{self.ha_url}/api",
            "ws": f"{ws}/websocket",
            "token": self.ha_token,
            "mode": "direct",
        }

    def available(self) -> bool:
        ep = self.endpoints()
        return bool(ep["token"]) and ep["api"].startswith("http")

    async def fetch_names(self, domains: list[str] | None = None) -> list[str]:
        """Sprechbare Namen (Friendly-Name + Alias) der gewaehlten Domains."""
        ep = self.endpoints()
        if not self.available():
            return []
        wanted = {d.strip() for d in (domains or []) if d.strip()}
        names: list[str] = []
        try:
            async with httpx.AsyncClient(
                base_url=ep["api"],
                headers={"Authorization": f"Bearer {ep['token']}"},
                timeout=15.0,
            ) as client:
                response = await client.get("/states")
                response.raise_for_status()
                for state in response.json():
                    entity_id = state.get("entity_id", "")
                    domain = entity_id.split(".", 1)[0]
                    if wanted and domain not in wanted:
                        continue
                    friendly = (state.get("attributes") or {}).get("friendly_name")
                    if friendly:
                        names.append(str(friendly))
        except Exception as exc:  # noqa: BLE001
            self.logger(f"[HOTWORDS] HA-States nicht ladbar: {exc}")
            return names

        aliases = await self._aliases(ep, wanted)
        names.extend(aliases)
        return names

    async def _aliases(self, ep: dict[str, str], domains: set[str]) -> list[str]:
        """Aliase aus der Entity-Registry (WebSocket, best effort)."""
        try:
            import websockets
        except Exception:  # noqa: BLE001
            return []
        try:
            async with websockets.connect(ep["ws"], max_size=None, open_timeout=10) as ws:
                message = json.loads(await ws.recv())
                if message.get("type") == "auth_required":
                    await ws.send(json.dumps({"type": "auth", "access_token": ep["token"]}))
                    message = json.loads(await ws.recv())
                if message.get("type") != "auth_ok":
                    return []
                await ws.send(json.dumps({"id": 1, "type": "config/entity_registry/list"}))
                while True:
                    incoming: dict[str, Any] = json.loads(await ws.recv())
                    if incoming.get("type") == "result" and incoming.get("id") == 1:
                        entries = incoming.get("result") or []
                        break
        except Exception as exc:  # noqa: BLE001
            self.logger(f"[HOTWORDS] Entity-Registry nicht ladbar: {exc}")
            return []

        aliases: list[str] = []
        for entry in entries:
            entity_id = entry.get("entity_id", "")
            if domains and entity_id.split(".", 1)[0] not in domains:
                continue
            for alias in entry.get("aliases") or []:
                if alias:
                    aliases.append(str(alias))
        return aliases
