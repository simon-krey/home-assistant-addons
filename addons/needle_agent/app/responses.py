"""Antworttexte fuer den Conversation-Agenten.

Needle erzeugt keine freien Saetze, deshalb werden die Antworten aus den
ausgefuehrten Tool-Calls und Ergebnissen gebaut. Die Templates sind im UI
ueberschreibbar (JSON).
"""

from __future__ import annotations

from typing import Any

DEFAULT_TEMPLATES: dict[str, str] = {
    "turn_on": "{name} ist jetzt an.",
    "turn_off": "{name} ist jetzt aus.",
    "get_state": "{name}: {state}.",
    "set_volume": "{name}: Lautstaerke auf {volume}.",
    "volume_up": "{name}: Lautstaerke auf {volume}.",
    "volume_down": "{name}: Lautstaerke auf {volume}.",
    "turn_on_light": "{name} ist jetzt an.",
    "turn_off_light": "{name} ist jetzt aus.",
    "set_brightness": "{name}: Helligkeit auf {volume} Prozent.",
    "turn_on_switch": "{name} ist jetzt an.",
    "turn_off_switch": "{name} ist jetzt aus.",
    "activate_scene": "Szene {name} ist aktiviert.",
    "set_media_volume": "{name}: Lautstaerke auf {volume}.",
    "set_temperature": "{name}: Temperatur auf {volume} Grad.",
    "get_entity_state": "{name}: {state}.",
    "refusal": "Das habe ich leider nicht verstanden.",
    "low_confidence": "Da bin ich mir nicht ganz sicher.",
    "error": "Beim Ausfuehren ist ein Fehler aufgetreten.",
    "dry_run_suffix": " (Testmodus)",
}


def _format(template: str, values: dict[str, Any]) -> str:
    class Safe(dict):
        def __missing__(self, key: str) -> str:  # noqa: D105
            return ""

    try:
        return template.format_map(Safe(values))
    except Exception:  # noqa: BLE001
        return template


def build_response(
    executed: list[dict[str, Any]],
    templates: dict[str, str],
    *,
    dry_run: bool = False,
    refusal: bool = False,
    low_confidence: bool = False,
    error: bool = False,
) -> str:
    if error:
        return templates.get("error", DEFAULT_TEMPLATES["error"])
    if low_confidence:
        return templates.get("low_confidence", DEFAULT_TEMPLATES["low_confidence"])
    if refusal or not executed:
        return templates.get("refusal", DEFAULT_TEMPLATES["refusal"])

    parts: list[str] = []
    for item in executed:
        name = item.get("name", "")
        template = templates.get(name)
        if not template:
            continue
        parts.append(
            _format(
                template,
                {
                    "name": item.get("entity_name") or name,
                    "state": item.get("state") or "",
                    "volume": item.get("volume") if item.get("volume") is not None else "",
                },
            )
        )

    response = " ".join(part.strip() for part in parts if part.strip())
    if not response:
        response = templates.get("refusal", DEFAULT_TEMPLATES["refusal"])
    if dry_run:
        response += templates.get("dry_run_suffix", DEFAULT_TEMPLATES["dry_run_suffix"])
    return response.strip()
