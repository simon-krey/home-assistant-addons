"""Namensauflösung fuer Geraete.

Kombiniert die Methoden, die sich in der Praxis bewaehrt haben:

* **Normalisierung** (Kleinschreibung, Umlaute, Sonderzeichen)
* **Kölner Phonetik** – deutsche STT-Varianten (Müller/Mueller, Meyer/Maier)
* **RapidFuzz** – schnelle, gute Aehnlichkeitsscores (token_set/partial/WRatio)
* **Aliase** aus der HA-Entity-Registry
* **Area/Floor-Kontext** – "das licht im wohnzimmer"
* **Confidence + Margin** – nur eindeutige Treffer akzeptieren
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

try:  # schnell + bessere Scores; Fallback falls nicht installiert
    from rapidfuzz import fuzz as _fuzz
except Exception:  # noqa: BLE001
    import difflib

    class _FuzzFallback:
        @staticmethod
        def partial_ratio(a: str, b: str) -> float:
            return difflib.SequenceMatcher(None, a, b).ratio() * 100

        @staticmethod
        def token_set_ratio(a: str, b: str) -> float:
            return difflib.SequenceMatcher(None, a, b).ratio() * 100

        @staticmethod
        def WRatio(a: str, b: str) -> float:
            return difflib.SequenceMatcher(None, a, b).ratio() * 100

    _fuzz = _FuzzFallback()  # type: ignore[assignment]

from .entities import EntityInfo, normalize

# Stichwoerter fuer Geraetetypen ("das licht", "der fernseher")
TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "light": (
        "licht", "lichter", "lampe", "lampen", "leuchte", "leuchten", "strahler",
        "spot", "deckenlampe", "stehlampe", "tischlampe", "schreibtischlampe",
        "nachttischlampe", "birne",
    ),
    "media_player": (
        "fernseher", "fernseh", "tv", "lautsprecher", "radio", "boxen", "soundbar",
        "musikanlage",
    ),
    "switch": (
        "steckdose", "steckdosen", "schalter", "kaffeemaschine", "kaffemaschine",
        "maschine", "ventilator", "pumpe",
    ),
    "climate": ("heizung", "thermostat", "klimaanlage", "klima"),
}


def type_domain(text: str) -> str | None:
    """Domain aus einem Geraetetyp-Stichwort ableiten."""
    normalized = normalize(text)
    for domain, keywords in TYPE_KEYWORDS.items():
        for keyword in keywords:
            if normalize(keyword) in normalized:
                return domain
    return None

# ---------------------------------------------------------------------------
# Kölner Phonetik
# ---------------------------------------------------------------------------
def koelner_phonetik(word: str) -> str:
    text = (word or "").upper()
    text = text.replace("Ä", "A").replace("Ö", "O").replace("Ü", "U").replace("ß", "S")
    text = re.sub(r"[^A-Z]", "", text)
    codes: list[str] = []
    for index, char in enumerate(text):
        previous = text[index - 1] if index > 0 else ""
        following = text[index + 1] if index + 1 < len(text) else ""
        if char in "AEIJOUY":
            code = "0"
        elif char == "H":
            code = ""
        elif char == "B":
            code = "1"
        elif char == "P":
            code = "3" if following == "H" else "1"
        elif char in "DT":
            code = "8" if following in "CSZ" else "2"
        elif char in "FVW":
            code = "3"
        elif char in "GKQ":
            code = "4"
        elif char == "C":
            if previous == "S" and following == "H":
                code = ""  # Teil von "sch"
            elif index == 0:
                code = "4" if following in "AHKLOQRUX" else "8"
            else:
                code = "4" if previous in "SZ" and following in "AHKLOQRUX" else "8"
        elif char == "X":
            code = "8"
        elif char == "L":
            code = "5"
        elif char in "MN":
            code = "6"
        elif char == "R":
            code = "7"
        elif char in "SZ":
            code = "8"
        else:
            code = "0"
        codes.append(code)

    joined = "".join(codes)
    collapsed: list[str] = []
    for char in joined:
        if not collapsed or collapsed[-1] != char:
            collapsed.append(char)
    return "".join(char for i, char in enumerate(collapsed) if not (char == "0" and i > 0))


def _phonetic_tokens(text: str) -> set[str]:
    return {koelner_phonetik(token) for token in normalize(text).split() if token}


# ---------------------------------------------------------------------------
# Auflösung
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    entity: EntityInfo
    score: float
    reason: str


@dataclass
class HomeContext:
    """Zusatzwissen aus Home Assistant (Areas/Floors)."""

    area_aliases: dict[str, str] = field(default_factory=dict)  # normalisiert -> Area-Name
    floor_of_area: dict[str, str] = field(default_factory=dict)  # Area-Name -> Floor-Name


class Resolver:
    def __init__(
        self,
        entities: list[EntityInfo],
        context: HomeContext | None = None,
        *,
        min_score: float = 0.72,
        min_margin: float = 0.12,
        floor: float = 0.55,
        tool_min_score: float = 0.6,
    ) -> None:
        self.entities = entities
        self.context = context or HomeContext()
        self.min_score = min_score
        self.min_margin = min_margin
        self.floor = floor
        self.tool_min_score = tool_min_score
        self._names: list[tuple[str, EntityInfo, str, bool]] = []
        for entity in entities:
            for name in entity.spoken_names:
                key = normalize(name)
                if key:
                    self._names.append((key, entity, name, name != entity.name))
        self._areas: list[tuple[str, str]] = []
        for entity in entities:
            if entity.area:
                self._areas.append((normalize(entity.area), entity.area))
        for alias, area in self.context.area_aliases.items():
            self._areas.append((normalize(alias), area))

    # -- Bereiche ----------------------------------------------------------
    def area_in(self, text: str) -> str | None:
        normalized = normalize(text)
        best: str | None = None
        best_length = 0
        for key, area in self._areas:
            if key and key in normalized and len(key) > best_length:
                best, best_length = area, len(key)
        return best

    def floor_in(self, text: str) -> str | None:
        normalized = normalize(text)
        for area, floor in self.context.floor_of_area.items():
            if floor and normalize(floor) in normalized:
                return floor
        return None

    # -- Namenssuche -------------------------------------------------------
    def mentions(self, text: str, domain: str | None = None) -> list[Candidate]:
        normalized = normalize(text)
        if not normalized:
            return []
        text_tokens = set(normalized.split())
        phonetic = _phonetic_tokens(text)
        results: dict[str, Candidate] = {}

        for key, entity, name, is_alias in self._names:
            if domain and entity.domain != domain:
                continue
            score = 0.0
            reason = ""

            if key in normalized:
                score = 1.0 if not is_alias else 0.97
                reason = f"Name/Alias '{name}'"
            else:
                name_tokens = set(key.split())
                hits = name_tokens & text_tokens
                overlap = len(hits) / len(name_tokens) if name_tokens else 0.0
                partial = _fuzz.partial_ratio(key, normalized) / 100.0
                token_set = _fuzz.token_set_ratio(key, normalized) / 100.0
                name_phon = {koelner_phonetik(token) for token in name_tokens}
                phonetic_hit = bool(name_phon) and name_phon <= phonetic
                score = 0.55 * overlap + 0.35 * partial + 0.10 * token_set
                if phonetic_hit:
                    score = max(score, 0.9)
                if hits:
                    longest = max(len(token) for token in hits)
                    if len(name_tokens) == 1:
                        score = max(score, 0.9)
                    else:
                        score = max(score, 0.75 if longest >= 6 else 0.7)
                if phonetic_hit:
                    reason = f"phonetisch '{name}'"
                elif hits:
                    reason = f"Worttreffer '{name}'"

            if score < self.floor:
                continue
            current = results.get(entity.entity_id)
            if current is None or score > current.score:
                results[entity.entity_id] = Candidate(entity, round(score, 3), reason)

        return sorted(results.values(), key=lambda c: c.score, reverse=True)

    def resolve(
        self,
        text: str,
        *,
        domain: str | None = None,
        area: str | None = None,
    ) -> list[Candidate]:
        """Kandidaten inkl. Area+Domain-Aufloesung."""
        domain = domain or type_domain(text)
        candidates = self.mentions(text, domain=domain)

        detected_area = area or self.area_in(text)
        if detected_area:
            filtered = [c for c in candidates if c.entity.area == detected_area]
            if filtered:
                candidates = filtered
            elif not candidates and domain:
                # Kein Namens-, aber Area+Domain-Treffer ("das licht im wohnzimmer")
                in_area = [
                    e for e in self.entities if e.area == detected_area and e.domain == domain
                ]
                if len(in_area) == 1:
                    candidates = [
                        Candidate(in_area[0], 0.8, f"Area '{detected_area}' + Domain '{domain}'")
                    ]
        return candidates

    def best(
        self,
        text: str,
        *,
        domain: str | None = None,
        area: str | None = None,
        min_score: float | None = None,
        min_margin: float | None = None,
    ) -> Candidate | None:
        """Eindeutig bester Treffer oder ``None`` (dann uebernimmt Needle)."""
        score_threshold = self.min_score if min_score is None else min_score
        margin_threshold = self.min_margin if min_margin is None else min_margin
        candidates = self.resolve(text, domain=domain, area=area)
        if not candidates:
            return None
        top = candidates[0]
        if top.score < score_threshold:
            return None
        if len(candidates) > 1 and (top.score - candidates[1].score) < margin_threshold:
            return None  # zu uneindeutig
        return top

    def annotate(self, text: str, domain: str | None = None) -> tuple[str, Candidate | None]:
        """Geraetehinweis an den Text haengen, damit Needle den Namen kennt."""
        candidate = self.best(text, domain=domain)
        if candidate is None:
            return text, None
        return f"{text} [Gerät: {candidate.entity.name}]", candidate
