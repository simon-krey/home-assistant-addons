"""Entity-Modell und Discovery-Helfer."""

from __future__ import annotations

from dataclasses import dataclass, field

# Domains, die der Agent in der ersten Version anbietet.
SUPPORTED_DOMAINS = ("light", "switch", "media_player")


@dataclass
class EntityInfo:
    entity_id: str
    name: str
    domain: str
    area: str | None = None
    device_class: str | None = None
    attributes: dict = field(default_factory=dict)

    @property
    def friendly_name(self) -> str:
        return self.name


def split_domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def filter_by_domains(
    entities: list[EntityInfo], domains: tuple[str, ...] = SUPPORTED_DOMAINS
) -> list[EntityInfo]:
    return [entity for entity in entities if entity.domain in domains]


def unique_name_index(entities: list[EntityInfo]) -> dict[str, EntityInfo]:
    """Name -> Entity. Bei doppelten Namen wird der spaetere verworfen."""
    index: dict[str, EntityInfo] = {}
    for entity in entities:
        index.setdefault(entity.name, entity)
    return index
