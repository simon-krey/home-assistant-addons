"""Entity-Modell inklusive Namensauflösung.

Home Assistant loest gesprochene Namen ueber Entity-Namen, **Aliase** und
Bereiche auf. Genau das brauchen wir hier auch: der Agent bekommt vom Modell
oft nicht den exakten Namen ("schreibtischlampe" statt "Schreibtischlampe
Büro"). Deshalb werden Namen normalisiert und unscharf aufgeloest.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

SUPPORTED_DOMAINS = ("light", "switch", "media_player", "scene", "climate", "fan", "input_boolean")

_UMLAUTS = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


@dataclass
class EntityInfo:
    entity_id: str
    name: str
    domain: str
    area: str | None = None
    state: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def spoken_names(self) -> list[str]:
        return [self.name, *self.aliases]


def split_domain(entity_id: str) -> str:
    return entity_id.split(".", 1)[0] if "." in entity_id else ""


def normalize(text: str) -> str:
    value = (text or "").lower()
    for source, target in _UMLAUTS:
        value = value.replace(source, target)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def build_name_index(entities: list[EntityInfo]) -> dict[str, EntityInfo]:
    index: dict[str, EntityInfo] = {}
    for entity in entities:
        for name in entity.spoken_names:
            key = normalize(name)
            if key:
                index.setdefault(key, entity)
    return index


def resolve_entity(index: dict[str, EntityInfo], spoken: str | None) -> EntityInfo | None:
    """Exakt -> Teiltreffer -> unscharf -> Token-Ueberlappung."""
    if not spoken:
        return None
    key = normalize(spoken)
    if not key:
        return None
    if key in index:
        return index[key]

    # Teiltreffer ("schreibtischlampe" <-> "schreibtischlampe buero")
    partial = [(name, entity) for name, entity in index.items() if key in name or name in key]
    if partial:
        partial.sort(key=lambda item: (abs(len(item[0]) - len(key)), len(item[0])))
        return partial[0][1]

    # Unscharf
    close = difflib.get_close_matches(key, list(index), n=1, cutoff=0.62)
    if close:
        return index[close[0]]

    # Token-Ueberlappung
    key_tokens = set(key.split())
    best: EntityInfo | None = None
    best_score = 0.0
    for name, entity in index.items():
        tokens = set(name.split())
        if not tokens:
            continue
        score = len(key_tokens & tokens) / len(key_tokens | tokens)
        if score > best_score:
            best_score, best = score, entity
    if best_score >= 0.5:
        return best
    return None


def unique_names(entities: list[EntityInfo]) -> list[str]:
    """Alle sprechbaren Namen (Name + Aliase), dedupliziert und stabil sortiert."""
    seen: set[str] = set()
    names: list[str] = []
    for entity in entities:
        for name in entity.spoken_names:
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def canonical_names(entities: list[EntityInfo]) -> list[str]:
    """Nur die kanonischen Entity-Namen.

    Aliase gehoeren **nicht** ins Decode-Grammar: zwei aehnliche Enum-Werte
    ("Schreibtischlampe" und "Schreibtisch") verwirren Needles Tool-Auswahl.
    Aliase werden stattdessen ueber ``resolve_mentions`` als Hinweis in den
    System-Prompt gegeben.
    """
    seen: set[str] = set()
    names: list[str] = []
    for entity in entities:
        if entity.name and entity.name not in seen:
            seen.add(entity.name)
            names.append(entity.name)
    return names


def resolve_mentions(text: str, entities: list[EntityInfo]) -> list[tuple[str, EntityInfo]]:
    """Im Text erwaehnte Geraete (Name oder Alias) finden."""
    normalized = normalize(text)
    if not normalized:
        return []
    matches: list[tuple[int, str, EntityInfo]] = []
    for entity in entities:
        for name in entity.spoken_names:
            key = normalize(name)
            if key and key in normalized:
                matches.append((len(key), name, entity))
                break
    matches.sort(key=lambda item: item[0], reverse=True)
    seen: set[str] = set()
    result: list[tuple[str, EntityInfo]] = []
    for _, name, entity in matches:
        if entity.entity_id in seen:
            continue
        seen.add(entity.entity_id)
        result.append((name, entity))
    return result
