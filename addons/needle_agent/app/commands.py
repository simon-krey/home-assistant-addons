"""Deterministischer Kommando-Parser (Fast-Path).

Fuer die haeufigen Kommandos braucht es kein Modell: Aktion (an/aus/dimmen/
Zustand/Lautstaerke/Temperatur) wird ueber deutsche Schluesselwoerter erkannt,
das Geraet ueber den :class:`Resolver` (Fuzzy + Phonetik + Area). Ist beides
eindeutig, wird direkt ausgefuehrt – schnell und reproduzierbar. Alles andere
geht an Needle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .entities import EntityInfo, normalize
from .matching import Resolver, type_domain

ON_WORDS = {
    "an", "ein", "einschalten", "einschalte", "anschalten", "anschalte",
    "anmachen", "anmache", "aktiviere", "aktivieren", "anschalten",
}
OFF_WORDS = {
    "aus", "ausschalten", "ausschalte", "ausmachen", "ausmache",
    "deaktiviere", "deaktivieren", "abschalten", "abschalte",
}
DIM_WORDS = {"dimme", "dimmen", "dimmt", "helligkeit", "dunkler", "heller", "dimmen"}
VOLUME_WORDS = {"lautstaerke", "lautstarke", "volume", "lauter", "leiser"}
TEMP_WORDS = {"temperatur", "grad", "waermer", "kuehler", "heizung", "thermostat"}
STATE_WORDS = {"zustand", "status", "wie", "ist", "sind", "an oder aus"}

DOMAIN_FOR_ACTION = {
    "set_brightness": "light",
    "set_volume": "media_player",
    "volume_up": "media_player",
    "volume_down": "media_player",
    "set_temperature": "climate",
}


@dataclass
class Command:
    action: str
    entity: EntityInfo
    value: float | None = None
    reason: str = ""


def _number(text: str, maximum: int = 100) -> float | None:
    """Zahl aus dem Originaltext (Dezimalpunkt bleibt erhalten)."""
    match = re.search(r"(\d{1,3})(?:[.,](\d+))?", text)
    if not match:
        return None
    value = float(f"{match.group(1)}.{match.group(2)}" if match.group(2) else match.group(1))
    return value if value <= maximum else None


class CommandParser:
    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def parse(self, text: str) -> Command | None:
        normalized = normalize(text)
        tokens = set(normalized.split())
        if not normalized:
            return None

        # 1) Mengen-Kommandos (eindeutige Zahlen) – Zahl aus dem Originaltext
        if tokens & DIM_WORDS:
            value = _number(text, 100)
            if value is not None:
                return self._with_entity("set_brightness", text, value, "dimmen")
        if tokens & VOLUME_WORDS:
            raw = _number(text, 100)
            if raw is not None:
                value = raw if raw <= 1 else raw / 100.0
                return self._with_entity("set_volume", text, value, "lautstaerke")
            if tokens & {"lauter", "leiser"}:
                action = "volume_up" if "lauter" in tokens else "volume_down"
                return self._with_entity(action, text, None, "relative lautstaerke")
        if tokens & TEMP_WORDS:
            value = _number(text, 40)
            if value is not None:
                return self._with_entity("set_temperature", text, value, "temperatur")

        # 2) Zustandsfrage
        if tokens & STATE_WORDS:
            command = self._with_entity("get_state", text, None, "zustand")
            if command is not None:
                return command

        # 3) Ein/Aus
        if tokens & OFF_WORDS:
            command = self._with_entity("turn_off", text, None, "aus")
            if command is not None:
                return command
        if tokens & ON_WORDS:
            command = self._with_entity("turn_on", text, None, "an")
            if command is not None:
                return command
        return None

    def _with_entity(
        self, action: str, text: str, value: float | None, reason: str
    ) -> Command | None:
        domain = DOMAIN_FOR_ACTION.get(action) or type_domain(text)
        candidate = self.resolver.best(text, domain=domain)
        if candidate is None:
            return None
        return Command(action=action, entity=candidate.entity, value=value, reason=candidate.reason)


def action_to_service(command: Command) -> tuple[str, str, dict]:
    """Aktion -> (domain, service, service_data)."""
    entity = command.entity
    if command.action == "turn_on":
        return entity.domain, "turn_on", {}
    if command.action == "turn_off":
        return entity.domain, "turn_off", {}
    if command.action == "set_brightness":
        return "light", "turn_on", {"brightness_pct": int(command.value or 100)}
    if command.action == "set_volume":
        return "media_player", "volume_set", {"volume_level": float(command.value or 0.5)}
    if command.action in ("volume_up", "volume_down"):
        return "media_player", "volume_set", {"volume_level": float(command.value or 0.5)}
    if command.action == "set_temperature":
        return "climate", "set_temperature", {"temperature": float(command.value or 20)}
    return entity.domain, "turn_on", {}
