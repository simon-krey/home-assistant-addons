"""Gemeinsames, synchrones Interface fuer Home-Assistant-Backends.

Der Mock und der echte WebSocket-Client implementieren dieselbe Schnittstelle,
damit die Tools unabhaengig vom Transport funktionieren.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .entities import EntityInfo


@runtime_checkable
class HomeAssistantBackend(Protocol):
    @property
    def connected(self) -> bool: ...

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def get_state(self, entity_id: str) -> dict[str, Any] | None: ...

    def get_states(self) -> dict[str, dict[str, Any]]: ...

    def get_entities(self) -> list[EntityInfo]: ...
