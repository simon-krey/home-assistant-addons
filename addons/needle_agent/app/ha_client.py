"""Home-Assistant-Zugriff.

Im Add-on ueber den Supervisor-Proxy (``http://supervisor/core``) mit
``SUPERVISOR_TOKEN`` – kein Long-Lived-Token noetig (``homeassistant_api: true``).
Ausserhalb des Add-ons alternativ direkte URL + Token.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from .entities import EntityInfo, split_domain
from .settings import Settings


class HomeAssistantClient:
    def __init__(self, settings: Settings, logger=print) -> None:
        self._settings = settings
        self._logger = logger
        self._http: httpx.AsyncClient | None = None
        self._states: dict[str, dict[str, Any]] = {}
        self.connected = False
        self.info: dict[str, Any] = {}
        self.mode = "?"

    def endpoints(self) -> dict[str, str]:
        token = os.getenv("SUPERVISOR_TOKEN")
        if token:
            return {
                "api": "http://supervisor/core/api",
                "ws": "ws://supervisor/core/websocket",
                "token": token,
                "mode": "supervisor",
            }
        url = (self._settings.ha_url or "").rstrip("/")
        ws = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return {
            "api": f"{url}/api",
            "ws": f"{ws}/websocket",
            "token": self._settings.ha_token or "",
            "mode": "direct",
        }

    async def connect(self) -> None:
        ep = self.endpoints()
        if not ep["token"] or not ep["api"].startswith("http"):
            raise RuntimeError("Kein HA-Zugang (SUPERVISOR_TOKEN oder ha_url + ha_token)")
        self.mode = ep["mode"]
        self._http = httpx.AsyncClient(
            base_url=ep["api"],
            headers={"Authorization": f"Bearer {ep['token']}"},
            timeout=20.0,
        )
        response = await self._http.get("/")
        response.raise_for_status()
        self.info = response.json()
        self.connected = True
        self._logger(f"[HA] verbunden ({self.mode}): {self.info.get('message', '')}")

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self.connected = False

    # -- States ------------------------------------------------------------
    async def get_states(self) -> dict[str, dict[str, Any]]:
        if self._http is None:
            raise RuntimeError("Nicht verbunden")
        response = await self._http.get("/states")
        response.raise_for_status()
        self._states = {state["entity_id"]: state for state in response.json()}
        return self._states

    def state(self, entity_id: str) -> dict[str, Any] | None:
        return self._states.get(entity_id)

    # -- Services ----------------------------------------------------------
    async def call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        if self._http is None:
            raise RuntimeError("Nicht verbunden")
        payload: dict[str, Any] = {}
        if target and target.get("entity_id"):
            payload["entity_id"] = target["entity_id"]
        if data:
            payload.update(data)
        response = await self._http.post(f"/services/{domain}/{service}", json=payload)
        response.raise_for_status()
        return response.json()

    # -- Entities ----------------------------------------------------------
    async def fetch_entities(self) -> list[EntityInfo]:
        states = await self.get_states()
        registry = await self._ws_registry()
        areas = {
            a["area_id"]: (a.get("name") or a["area_id"])
            for a in registry.get("config/area_registry/list") or []
        }
        device_area = {d["id"]: d.get("area_id") for d in registry.get("config/device_registry/list") or []}
        entries = registry.get("config/entity_registry/list") or []

        entities: list[EntityInfo] = []
        if entries:
            for entry in entries:
                entity_id = entry.get("entity_id", "")
                if not entity_id:
                    continue
                state = states.get(entity_id, {})
                attributes = state.get("attributes") or {}
                name = (
                    entry.get("name")
                    or attributes.get("friendly_name")
                    or entry.get("original_name")
                    or entity_id
                )
                area_id = entry.get("area_id") or device_area.get(entry.get("device_id"))
                entities.append(
                    EntityInfo(
                        entity_id=entity_id,
                        name=name,
                        domain=split_domain(entity_id),
                        area=areas.get(area_id) if area_id else None,
                        state=state.get("state"),
                    )
                )
        else:
            for entity_id, state in states.items():
                attributes = state.get("attributes") or {}
                entities.append(
                    EntityInfo(
                        entity_id=entity_id,
                        name=attributes.get("friendly_name") or entity_id,
                        domain=split_domain(entity_id),
                        state=state.get("state"),
                    )
                )
        return entities

    async def _ws_registry(self) -> dict[str, Any]:
        """Entity-/Area-/Device-Registry per WebSocket (best effort)."""
        ep = self.endpoints()
        commands = (
            "config/area_registry/list",
            "config/device_registry/list",
            "config/entity_registry/list",
        )
        try:
            import websockets

            async with websockets.connect(ep["ws"], max_size=None, open_timeout=10) as ws:
                message = json.loads(await ws.recv())
                if message.get("type") == "auth_required":
                    await ws.send(json.dumps({"type": "auth", "access_token": ep["token"]}))
                    message = json.loads(await ws.recv())
                if message.get("type") != "auth_ok":
                    return {}
                result: dict[str, Any] = {}
                for index, command in enumerate(commands, start=1):
                    await ws.send(json.dumps({"id": index, "type": command}))
                    while True:
                        incoming = json.loads(await ws.recv())
                        if incoming.get("type") == "result" and incoming.get("id") == index:
                            result[command] = incoming.get("result")
                            break
                return result
        except Exception as exc:  # noqa: BLE001
            self._logger(f"[HA] Registry per WebSocket nicht verfuegbar: {exc}")
            return {}
