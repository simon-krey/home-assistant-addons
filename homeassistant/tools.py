"""Home-Assistant-Tools fuer Needle.

Die Tool-Schemas werden dynamisch aus den Entities erzeugt. Die erlaubten
Geraetenamen landen als ``enum`` im Decode-Grammar, dadurch kann Needle nur
existierende Geraete waehlen. Python uebersetzt Name -> ``entity_id``.
"""

from __future__ import annotations

from typing import Any, Callable

from tools.registry import ToolRegistry, ToolSpec

from .base import HomeAssistantBackend
from .entities import SUPPORTED_DOMAINS, EntityInfo, filter_by_domains, unique_name_index
from .state import StateCache

MAX_TOOLS = 5

DryRunFlag = bool | Callable[[], bool]


def _string_enum(names: list[str], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": names, "description": description}


def build_ha_registry(
    backend: HomeAssistantBackend,
    state_cache: StateCache | None = None,
    dry_run: DryRunFlag = True,
    domains: tuple[str, ...] = SUPPORTED_DOMAINS,
) -> ToolRegistry:
    def is_dry_run() -> bool:
        return dry_run() if callable(dry_run) else dry_run

    entities = filter_by_domains(backend.get_entities(), domains)
    index = unique_name_index(entities)

    lights = [e.name for e in entities if e.domain == "light"]
    switches = [e.name for e in entities if e.domain == "switch"]
    media_players = [e.name for e in entities if e.domain == "media_player"]
    all_names = [e.name for e in entities]

    def resolve(args: dict[str, Any]) -> EntityInfo:
        name = args.get("entity_id")
        entity = index.get(name)
        if entity is None:
            raise ValueError(f"Unbekanntes Geraet: {name!r}")
        return entity

    def perform(
        domain: str,
        service: str,
        entity: EntityInfo,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = {"entity_id": entity.entity_id}
        if is_dry_run():
            return {
                "dry_run": True,
                "domain": domain,
                "service": service,
                "target": target,
                "data": data or {},
            }
        result = backend.call_service(domain, service, target, data)
        if state_cache is not None and isinstance(result, dict):
            for state in result.get("changed_states") or []:
                if state.get("entity_id"):
                    state_cache.update(state["entity_id"], state)
        return {
            "entity_id": entity.entity_id,
            "domain": domain,
            "service": service,
            "result": result,
        }

    specs: list[ToolSpec] = []

    if lights:
        specs.append(
            ToolSpec(
                name="turn_on_light",
                description="Turn on a light. Pick the device by its spoken name.",
                parameters={
                    "type": "object",
                    "properties": {"entity_id": _string_enum(lights, "the light to turn on")},
                    "required": ["entity_id"],
                },
                handler=lambda args: perform("light", "turn_on", resolve(args)),
            )
        )
        specs.append(
            ToolSpec(
                name="turn_off_light",
                description="Turn off a light. Pick the device by its spoken name.",
                parameters={
                    "type": "object",
                    "properties": {"entity_id": _string_enum(lights, "the light to turn off")},
                    "required": ["entity_id"],
                },
                handler=lambda args: perform("light", "turn_off", resolve(args)),
            )
        )

    if switches:
        specs.append(
            ToolSpec(
                name="turn_on_switch",
                description="Turn on a switch or appliance.",
                parameters={
                    "type": "object",
                    "properties": {"entity_id": _string_enum(switches, "the switch to turn on")},
                    "required": ["entity_id"],
                },
                handler=lambda args: perform("switch", "turn_on", resolve(args)),
            )
        )

    def get_entity_state(args: dict[str, Any]) -> dict[str, Any]:
        entity = resolve(args)
        state = None
        if state_cache is not None:
            state = state_cache.get(entity.entity_id)
        if state is None:
            state = backend.get_state(entity.entity_id)
        if state is None:
            raise KeyError(f"entity not found: {entity.entity_id}")
        return {
            "entity_id": entity.entity_id,
            "state": state.get("state"),
            "attributes": state.get("attributes", {}),
        }

    specs.append(
        ToolSpec(
            name="get_entity_state",
            description="Get the current state of a device.",
            parameters={
                "type": "object",
                "properties": {"entity_id": _string_enum(all_names, "the device to query")},
                "required": ["entity_id"],
            },
            handler=get_entity_state,
        )
    )

    if media_players:
        specs.append(
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
                handler=lambda args: perform(
                    "media_player",
                    "volume_set",
                    resolve(args),
                    {"volume_level": args.get("volume")},
                ),
            )
        )

    if len(specs) > MAX_TOOLS:
        raise ValueError(
            f"{len(specs)} Tools > {MAX_TOOLS}: Needles Tool-Retrieval wuerde greifen. "
            "Bitte Domains reduzieren oder tool_index_path nutzen."
        )

    return ToolRegistry(specs)
