"""Entity-Modell."""

from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_DOMAINS = ("light", "switch", "media_player", "scene", "climate", "fan", "input_boolean")


@dataclass
class EntityInfo:
    entity_id: str
    name: str
    domain: str
    area: str | None = None
    state: str | None = None

    @property
    def spoken_name(self) -> str:
        return self.name


def split_domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def unique_name_index(entities: list[EntityInfo]) -> dict[str, EntityInfo]:
    index: dict[str, EntityInfo] = {}
    for entity in entities:
        index.setdefault(entity.name, entity)
    return index
