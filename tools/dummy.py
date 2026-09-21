"""Dummy-Tools fuer Phase 3 (Needle ohne Home Assistant).

Die Entity-Namen werden als ``enum`` in die Tool-Schemas geschrieben, damit
Needle nur gueltige Namen waehlen kann. Ausgefuehrt wird nichts – die Handler
liefern nur ein simuliertes Ergebnis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .registry import ToolRegistry, ToolSpec


@dataclass(frozen=True)
class DummyEntity:
    entity_id: str
    name: str
    domain: str


DUMMY_ENTITIES: list[DummyEntity] = [
    DummyEntity("light.wohnzimmer_decke", "Wohnzimmer Deckenlampe", "light"),
    DummyEntity("light.wohnzimmer_stehlampe", "Wohnzimmer Stehlampe", "light"),
    DummyEntity("light.schlafzimmer", "Schlafzimmer Licht", "light"),
    DummyEntity("light.kueche", "Kuechenlicht", "light"),
    DummyEntity("switch.kaffeemaschine", "Kaffeemaschine", "switch"),
    DummyEntity("media_player.wohnzimmer_tv", "Wohnzimmer Fernseher", "media_player"),
]


def _string_enum(names: list[str], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": names, "description": description}


def build_dummy_registry(entities: list[DummyEntity] | None = None) -> ToolRegistry:
    entities = entities or DUMMY_ENTITIES
    by_name = {entity.name: entity for entity in entities}
    lights = [e.name for e in entities if e.domain == "light"]
    switches = [e.name for e in entities if e.domain == "switch"]
    media_players = [e.name for e in entities if e.domain == "media_player"]
    all_names = [e.name for e in entities]

    def resolve(args: dict[str, Any]) -> DummyEntity:
        name = args.get("entity_id")
        if name not in by_name:
            raise ValueError(f"Unbekanntes Geraet: {name!r}")
        return by_name[name]

    def turn_on_light(args: dict[str, Any]) -> dict[str, Any]:
        entity = resolve(args)
        return {"entity_id": entity.entity_id, "state": "on", "simulated": True}

    def turn_off_light(args: dict[str, Any]) -> dict[str, Any]:
        entity = resolve(args)
        return {"entity_id": entity.entity_id, "state": "off", "simulated": True}

    def turn_on_switch(args: dict[str, Any]) -> dict[str, Any]:
        entity = resolve(args)
        return {"entity_id": entity.entity_id, "state": "on", "simulated": True}

    def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        entity = resolve(args)
        return {"entity_id": entity.entity_id, "state": "on", "simulated": True}

    def set_media_volume(args: dict[str, Any]) -> dict[str, Any]:
        entity = resolve(args)
        return {
            "entity_id": entity.entity_id,
            "volume": args.get("volume"),
            "simulated": True,
        }

    return ToolRegistry(
        [
            ToolSpec(
                name="turn_on_light",
                description="Turn on a light. Choose the device by its spoken name.",
                parameters={
                    "type": "object",
                    "properties": {
                        "entity_id": _string_enum(lights, "the light to turn on")
                    },
                    "required": ["entity_id"],
                },
                handler=turn_on_light,
            ),
            ToolSpec(
                name="turn_off_light",
                description="Turn off a light. Choose the device by its spoken name.",
                parameters={
                    "type": "object",
                    "properties": {
                        "entity_id": _string_enum(lights, "the light to turn off")
                    },
                    "required": ["entity_id"],
                },
                handler=turn_off_light,
            ),
            ToolSpec(
                name="turn_on_switch",
                description="Turn on a switch or appliance.",
                parameters={
                    "type": "object",
                    "properties": {
                        "entity_id": _string_enum(switches, "the switch to turn on")
                    },
                    "required": ["entity_id"],
                },
                handler=turn_on_switch,
            ),
            ToolSpec(
                name="get_entity_state",
                description="Get the current state of a device.",
                parameters={
                    "type": "object",
                    "properties": {
                        "entity_id": _string_enum(all_names, "the device to query")
                    },
                    "required": ["entity_id"],
                },
                handler=get_entity_state,
            ),
            ToolSpec(
                name="set_media_volume",
                description="Set the volume of a media player, from 0.0 to 1.0.",
                parameters={
                    "type": "object",
                    "properties": {
                        "entity_id": _string_enum(media_players, "the media player"),
                        "volume": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "target volume",
                        },
                    },
                    "required": ["entity_id", "volume"],
                },
                handler=set_media_volume,
            ),
        ]
    )
