"""Mock-Backend ohne echte Home-Assistant-Instanz.

Ermoeglicht den kompletten Pipeline-Test (Discovery, State, Tool-Calls) ohne
Netzwerk. Es wird nichts ausgefuehrt, aber der Aufruf wird protokolliert und
der simulierte Zustand angepasst.
"""

from __future__ import annotations

from typing import Any

from .entities import EntityInfo

DEFAULT_ENTITIES: list[EntityInfo] = [
    EntityInfo("light.wohnzimmer_decke", "Wohnzimmer Deckenlampe", "light", area="Wohnzimmer"),
    EntityInfo("light.wohnzimmer_stehlampe", "Wohnzimmer Stehlampe", "light", area="Wohnzimmer"),
    EntityInfo("light.schlafzimmer", "Schlafzimmer Licht", "light", area="Schlafzimmer"),
    EntityInfo("light.kueche", "Kuechenlicht", "light", area="Kueche"),
    EntityInfo("switch.kaffeemaschine", "Kaffeemaschine", "switch", area="Kueche"),
    EntityInfo(
        "media_player.wohnzimmer_tv", "Wohnzimmer Fernseher", "media_player", area="Wohnzimmer"
    ),
    EntityInfo(
        "sensor.wohnzimmer_temperatur", "Wohnzimmer Temperatur", "sensor", area="Wohnzimmer"
    ),
]

DEFAULT_STATES: dict[str, str] = {
    "light.wohnzimmer_decke": "off",
    "light.wohnzimmer_stehlampe": "on",
    "light.schlafzimmer": "off",
    "light.kueche": "off",
    "switch.kaffeemaschine": "off",
    "media_player.wohnzimmer_tv": "on",
    "sensor.wohnzimmer_temperatur": "21.4",
}


class MockHomeAssistant:
    def __init__(
        self,
        entities: list[EntityInfo] | None = None,
        states: dict[str, str] | None = None,
    ) -> None:
        self._entities = list(entities or DEFAULT_ENTITIES)
        initial = states or DEFAULT_STATES
        by_id = {entity.entity_id: entity for entity in self._entities}
        self._states: dict[str, dict[str, Any]] = {}
        for entity_id, state in initial.items():
            entity = by_id.get(entity_id)
            self._states[entity_id] = {
                "entity_id": entity_id,
                "state": state,
                "attributes": {"friendly_name": entity.name if entity else entity_id},
            }
        self._connected = False
        self.calls: list[dict[str, Any]] = []

    # -- Backend-Interface -------------------------------------------------
    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self._connected = True

    def close(self) -> None:
        self._connected = False

    def call_service(
        self,
        domain: str,
        service: str,
        target: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "domain": domain,
            "service": service,
            "target": target or {},
            "data": data or {},
        }
        self.calls.append(record)

        entity_ids: list[str] = []
        if target and target.get("entity_id"):
            raw = target["entity_id"]
            entity_ids = raw if isinstance(raw, list) else [raw]

        changed: list[dict[str, Any]] = []
        for entity_id in entity_ids:
            current = self._states.get(entity_id)
            if current is None:
                raise KeyError(f"entity not found: {entity_id}")
            new_state = dict(current)
            new_state["attributes"] = dict(current.get("attributes", {}))
            if service == "turn_on":
                new_state["state"] = "on"
            elif service == "turn_off":
                new_state["state"] = "off"
            elif service == "toggle":
                new_state["state"] = "off" if current["state"] == "on" else "on"
            if data and "volume_level" in data:
                new_state["attributes"]["volume_level"] = data["volume_level"]
            self._states[entity_id] = new_state
            changed.append(new_state)

        return {"changed_states": changed}

    def get_state(self, entity_id: str) -> dict[str, Any] | None:
        return self._states.get(entity_id)

    def get_states(self) -> dict[str, dict[str, Any]]:
        return dict(self._states)

    def get_entities(self) -> list[EntityInfo]:
        return list(self._entities)
