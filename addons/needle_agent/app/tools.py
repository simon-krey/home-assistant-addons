"""Dynamische Tools aus Home-Assistant-Entities.

Bewusst **domaenenspezifische** Tool-Namen (``turn_on_light``,
``turn_off_switch``, ...). Tests zeigen: das ist bei deutschen Kommandos
deutlich zuverlaessiger als generische ``turn_on``/``on: bool``-Tools.

Needle rendert **fuenf oder weniger** Tools direkt; ab sechs greift
Tool-Retrieval und ungewaehlte Tools sind unerreichbar. Deshalb ist die
Auswahl konfigurierbar und standardmaessig auf 5 begrenzt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .entities import EntityInfo, canonical_names
from .matching import Resolver

MAX_DIRECT_TOOLS = 5
SWITCH_DOMAINS = ("switch", "fan", "input_boolean")

# Reihenfolge = Prioritaet fuer die Standardauswahl.
AVAILABLE_TOOLS = (
    "turn_on_light",
    "turn_off_light",
    "turn_on_switch",
    "turn_off_switch",
    "get_entity_state",
    "set_media_volume",
    "set_brightness",
    "set_temperature",
    "activate_scene",
)
DEFAULT_TOOLS = (
    "turn_on_light",
    "turn_off_light",
    "turn_on_switch",
    "turn_off_switch",
    "get_entity_state",
)


@dataclass
class ToolAction:
    domain: str
    service: str | None
    target: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)
    read_state: bool = False


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    build: Callable[[dict[str, Any], "ToolSet"], ToolAction]


class ToolSet:
    def __init__(
        self,
        specs: list[ToolSpec],
        entities: list[EntityInfo],
        resolver: Resolver | None = None,
    ) -> None:
        self._specs = {spec.name: spec for spec in specs}
        self.entities = entities
        self.resolver = resolver or Resolver(entities)
        self.by_id = {entity.entity_id: entity for entity in entities}

    def __len__(self) -> int:
        return len(self._specs)

    def names(self) -> list[str]:
        return list(self._specs)

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {"name": s.name, "description": s.description, "parameters": s.parameters}
            for s in self._specs.values()
        ]

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def resolve(self, name: str, arguments: dict[str, Any]) -> ToolAction:
        spec = self._specs.get(name)
        if spec is None:
            raise ValueError(f"Unbekanntes Tool: {name}")
        return spec.build(dict(arguments or {}), self)

    def entity(self, name: str | None) -> EntityInfo:
        if name:
            for entity in self.entities:
                if name in entity.spoken_names:
                    return entity
            candidate = self.resolver.best(
                name, min_score=self.resolver.tool_min_score, min_margin=0.0
            )
            if candidate is not None:
                return candidate.entity
        raise ValueError(f"Unbekanntes Geraet: {name!r}")


def _enum(names: list[str], description: str) -> dict[str, Any]:
    return {"type": "string", "enum": names, "description": description}


# -- Action-Builder --------------------------------------------------------
def _light_on(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction("light", "turn_on", {"entity_id": e.entity_id})


def _light_off(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction("light", "turn_off", {"entity_id": e.entity_id})


def _brightness(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction("light", "turn_on", {"entity_id": e.entity_id},
                      {"brightness_pct": int(a.get("percent", 100))})


def _switch_on(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction(e.domain, "turn_on", {"entity_id": e.entity_id})


def _switch_off(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction(e.domain, "turn_off", {"entity_id": e.entity_id})


def _state(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction(e.domain, None, {"entity_id": e.entity_id}, read_state=True)


def _volume(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction("media_player", "volume_set", {"entity_id": e.entity_id},
                      {"volume_level": float(a.get("volume", 0.5))})


def _temperature(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction("climate", "set_temperature", {"entity_id": e.entity_id},
                      {"temperature": float(a.get("temperature", 20))})


def _scene(a, t: ToolSet) -> ToolAction:
    e = t.entity(a.get("entity_id"))
    return ToolAction("scene", "turn_on", {"entity_id": e.entity_id})


def build_toolset(
    entities: list[EntityInfo],
    domains: list[str] | None = None,
    enabled: list[str] | None = None,
    resolver: Resolver | None = None,
) -> ToolSet:
    allowed = set(domains) if domains else None
    pool = [e for e in entities if allowed is None or e.domain in allowed]

    lights = canonical_names([e for e in pool if e.domain == "light"])
    switches = canonical_names([e for e in pool if e.domain in SWITCH_DOMAINS])
    media = canonical_names([e for e in pool if e.domain == "media_player"])
    climate = canonical_names([e for e in pool if e.domain == "climate"])
    scenes = canonical_names([e for e in pool if e.domain == "scene"])
    all_names = canonical_names(pool)

    def spec(name: str, description: str, properties: dict, required: list[str],
             build) -> ToolSpec:
        return ToolSpec(
            name, description,
            {"type": "object", "properties": properties, "required": required},
            build,
        )

    factories: dict[str, Callable[[], ToolSpec | None]] = {
        "turn_on_light": lambda: spec("turn_on_light", "Turn on a light.",
            {"entity_id": _enum(lights, "the light to turn on")}, ["entity_id"], _light_on) if lights else None,
        "turn_off_light": lambda: spec("turn_off_light", "Turn off a light.",
            {"entity_id": _enum(lights, "the light to turn off")}, ["entity_id"], _light_off) if lights else None,
        "set_brightness": lambda: spec("set_brightness", "Set the brightness of a light, in percent.",
            {"entity_id": _enum(lights, "the light"),
             "percent": {"type": "integer", "minimum": 0, "maximum": 100}},
            ["entity_id", "percent"], _brightness) if lights else None,
        "turn_on_switch": lambda: spec("turn_on_switch", "Turn on a switch or appliance.",
            {"entity_id": _enum(switches, "the switch to turn on")}, ["entity_id"], _switch_on) if switches else None,
        "turn_off_switch": lambda: spec("turn_off_switch", "Turn off a switch or appliance.",
            {"entity_id": _enum(switches, "the switch to turn off")}, ["entity_id"], _switch_off) if switches else None,
        "activate_scene": lambda: spec("activate_scene", "Activate a scene.",
            {"entity_id": _enum(scenes, "the scene to activate")}, ["entity_id"], _scene) if scenes else None,
        "set_media_volume": lambda: spec("set_media_volume", "Set the volume of a media player, from 0.0 to 1.0.",
            {"entity_id": _enum(media, "the media player"),
             "volume": {"type": "number", "minimum": 0.0, "maximum": 1.0}},
            ["entity_id", "volume"], _volume) if media else None,
        "set_temperature": lambda: spec("set_temperature", "Set the target temperature of a climate device.",
            {"entity_id": _enum(climate, "the climate device"),
             "temperature": {"type": "number", "minimum": 5, "maximum": 35}},
            ["entity_id", "temperature"], _temperature) if climate else None,
        "get_entity_state": lambda: spec("get_entity_state", "Get the current state of a device.",
            {"entity_id": _enum(all_names, "the device to query")}, ["entity_id"], _state) if all_names else None,
    }

    selected = enabled if enabled else list(DEFAULT_TOOLS)
    specs: list[ToolSpec] = []
    for name in selected:
        factory = factories.get(name)
        if factory is None:
            continue
        built = factory()
        if built is not None:
            specs.append(built)
    return ToolSet(specs, pool, resolver=resolver)
