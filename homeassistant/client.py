"""Echter Home-Assistant-Client (WebSocket).

Die asynchrone Kommunikation laeuft in einem eigenen Event-Loop-Thread. Nach
aussen wird das synchrone ``HomeAssistantBackend``-Interface angeboten, damit
die restliche Pipeline thread-basiert bleiben kann.

Auth-Ablauf laut HA-Doku: ``auth_required`` -> ``auth`` -> ``auth_ok``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable

from websockets.asyncio.client import connect

from .entities import EntityInfo, split_domain
from .state import StateCache


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantAuthError(HomeAssistantError):
    pass


class HomeAssistantConnectionError(HomeAssistantError):
    pass


EventHandler = Callable[[dict[str, Any]], None]


class _AsyncHomeAssistant:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        reconnect: bool,
        reconnect_delay: float,
        state_cache: StateCache | None,
        on_state_changed: EventHandler | None,
        on_connected: Callable[[], None],
        on_disconnected: Callable[[], None],
        logger: Callable[[str], None],
    ) -> None:
        self.url = url
        self.token = token
        self.reconnect = reconnect
        self.reconnect_delay = reconnect_delay
        self.state_cache = state_cache
        self.on_state_changed = on_state_changed
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.logger = logger

        self.entities: list[EntityInfo] = []
        self._ws: Any = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._subscriptions: dict[int, EventHandler] = {}
        self._connected = asyncio.Event()
        self._closing = False

    # -- Verbindung --------------------------------------------------------
    async def _connect_and_auth(self) -> None:
        self._ws = await connect(
            self.url,
            max_size=None,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        )
        raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
        message = json.loads(raw)
        if message.get("type") == "auth_required":
            await self._ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            message = json.loads(raw)
        if message.get("type") != "auth_ok":
            raise HomeAssistantAuthError(
                f"Authentifizierung fehlgeschlagen: {message.get('type')} {message.get('message', '')}"
            )

    async def run(self) -> None:
        while not self._closing:
            try:
                await self._connect_and_auth()
                self._connected.set()
                await self._setup_session()
                self.on_connected()
                await self._read_loop()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 - Reconnect-Loop
                self.logger(f"[HA ERROR] {type(exc).__name__}: {exc}")
            finally:
                self._connected.clear()
                self.on_disconnected()
                self._fail_pending("Verbindung verloren")
                self._subscriptions.clear()
                ws, self._ws = self._ws, None
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self._closing or not self.reconnect:
                break
            self.logger(f"[HA] Reconnect in {self.reconnect_delay:.1f}s ...")
            await asyncio.sleep(self.reconnect_delay)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for raw in self._ws:
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "result":
                future = self._pending.pop(message.get("id"), None)
                if future is None or future.done():
                    continue
                if message.get("success"):
                    future.set_result(message.get("result"))
                else:
                    error = message.get("error") or {}
                    future.set_exception(
                        HomeAssistantError(f"{error.get('code')}: {error.get('message')}")
                    )
            elif kind == "event":
                handler = self._subscriptions.get(message.get("id"))
                if handler is not None:
                    try:
                        handler(message.get("event") or {})
                    except Exception as exc:  # noqa: BLE001
                        self.logger(f"[HA ERROR] Event-Handler: {exc}")

    async def _setup_session(self) -> None:
        states = await self.get_states()
        if self.state_cache is not None:
            self.state_cache.update_many(states)
        self.entities = await self.fetch_entities()
        await self.subscribe_events("state_changed", self._handle_state_changed)
        self.logger(f"[HA] verbunden, {len(self.entities)} Entities, {len(states)} States")

    def _handle_state_changed(self, event: dict[str, Any]) -> None:
        if self.state_cache is not None:
            self.state_cache.apply_state_changed(event)
        if self.on_state_changed is not None:
            self.on_state_changed(event)

    async def close(self) -> None:
        self._closing = True
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    # -- Kommandos ---------------------------------------------------------
    async def _command(self, payload: dict[str, Any], timeout: float = 15.0) -> tuple[int, Any]:
        if self._ws is None:
            raise HomeAssistantConnectionError("Nicht verbunden")
        self._id += 1
        message_id = self._id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self._ws.send(json.dumps({"id": message_id, **payload}))
        try:
            return message_id, await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(message_id, None)

    def _fail_pending(self, reason: str) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(HomeAssistantConnectionError(reason))
        self._pending.clear()

    async def call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "call_service",
            "domain": domain,
            "service": service,
        }
        if target:
            payload["target"] = target
        if data:
            payload["service_data"] = data
        _, result = await self._command(payload)
        return result or {}

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        _, result = await self._command({"type": "get_states"})
        for state in result or []:
            if state.get("entity_id") == entity_id:
                return state
        return None

    async def get_states(self) -> dict[str, dict[str, Any]]:
        _, result = await self._command({"type": "get_states"})
        return {state["entity_id"]: state for state in result or []}

    async def fetch_entities(self) -> list[EntityInfo]:
        _, areas = await self._command({"type": "config/area_registry/list"})
        _, devices = await self._command({"type": "config/device_registry/list"})
        _, entries = await self._command({"type": "config/entity_registry/list"})
        _, state_list = await self._command({"type": "get_states"})

        area_names = {a["area_id"]: (a.get("name") or a["area_id"]) for a in areas or []}
        device_area = {d["id"]: d.get("area_id") for d in devices or []}
        friendly = {
            s["entity_id"]: (s.get("attributes") or {}).get("friendly_name")
            for s in state_list or []
        }

        result: list[EntityInfo] = []
        for entry in entries or []:
            entity_id = entry.get("entity_id", "")
            if not entity_id:
                continue
            name = (
                entry.get("name")
                or friendly.get(entity_id)
                or entry.get("original_name")
                or entity_id
            )
            area_id = entry.get("area_id") or device_area.get(entry.get("device_id"))
            result.append(
                EntityInfo(
                    entity_id=entity_id,
                    name=name,
                    domain=split_domain(entity_id),
                    area=area_names.get(area_id) if area_id else None,
                    device_class=entry.get("device_class"),
                )
            )
        return result

    async def subscribe_events(self, event_type: str, handler: EventHandler) -> None:
        message_id, _ = await self._command(
            {"type": "subscribe_events", "event_type": event_type}
        )
        self._subscriptions[message_id] = handler


class WebSocketHomeAssistant:
    """Synchrone Fassade um ``_AsyncHomeAssistant``."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        reconnect: bool = True,
        reconnect_delay: float = 3.0,
        request_timeout: float = 15.0,
        state_cache: StateCache | None = None,
        on_state_changed: EventHandler | None = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.url = url
        self.token = token
        self._request_timeout = request_timeout
        self._logger = logger
        self._connected = threading.Event()
        self._stopped = False
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._thread_main, name="ha-loop", daemon=True)
        self._async = _AsyncHomeAssistant(
            url,
            token,
            reconnect=reconnect,
            reconnect_delay=reconnect_delay,
            state_cache=state_cache,
            on_state_changed=on_state_changed,
            on_connected=self._connected.set,
            on_disconnected=self._connected.clear,
            logger=logger,
        )

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    # -- Backend-Interface -------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def entities(self) -> list[EntityInfo]:
        return list(self._async.entities)

    def connect(self, timeout: float = 20.0) -> None:
        if not self.url:
            raise HomeAssistantError("HA_URL ist nicht gesetzt")
        if not self.token:
            raise HomeAssistantAuthError("HA_TOKEN ist nicht gesetzt")
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._async.run(), self._loop)
        if not self._connected.wait(timeout):
            raise HomeAssistantConnectionError(f"Timeout beim Verbinden mit {self.url}")

    def close(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            asyncio.run_coroutine_threadsafe(self._async.close(), self._loop).result(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)

    def _submit(self, coro: Any, timeout: float | None = None) -> Any:
        if self._stopped:
            raise HomeAssistantConnectionError("Client wurde geschlossen")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout or self._request_timeout)

    def call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._submit(self._async.call_service(domain, service, target, data))

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        return self._submit(self._async.get_state(entity_id))

    def get_states(self) -> dict[str, dict[str, Any]]:
        return self._submit(self._async.get_states())

    def get_entities(self) -> list[EntityInfo]:
        return self.entities
