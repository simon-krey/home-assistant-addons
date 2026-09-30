"""Aufbau der Engine inklusive Hotwords / Contextual Biasing.

Die Engine wird beim Start und bei Aenderungen neu gebaut. Hotwords stammen aus
zwei Quellen:

* der manuellen Liste in den Einstellungen,
* den Entity-Namen (+ Aliasen) aus Home Assistant (optional).

Contextual Biasing unterstuetzt sherpa-onnx nur fuer Offline-Transducer
(Parakeet v3); bei Streaming-Modellen (Kroko) werden die Hotwords zwar geladen,
aber nicht verwendet.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .engine import build_engine, resolve_spec
from .ha_client import HomeAssistantSource
from .hotwords import parse_list, write_hotwords
from .settings import DATA_DIR, Settings


def hotwords_path() -> Path:
    return DATA_DIR / "hotwords.txt"


def supports_hotwords(settings: Settings) -> bool:
    try:
        spec = resolve_spec(
            settings.model,
            settings.model_url or None,
            settings.model_type or None,
            settings.kind or None,
        )
    except Exception:  # noqa: BLE001
        return False
    return spec.kind == "parakeet"


async def resolve_hotwords(settings: Settings, logger=print) -> tuple[list[str], str | None]:
    """Manuelle + automatische Hotwords sammeln. Gibt (Wortliste, Fehlermeldung)."""
    words = parse_list(settings.hotwords)
    error: str | None = None
    if settings.hotwords_from_ha:
        source = HomeAssistantSource(settings.ha_url, settings.ha_token, logger=logger)
        if source.available():
            names = await source.fetch_names(settings.hotwords_domains_list())
            if names:
                words.extend(names)
            else:
                error = "keine Entity-Namen von Home Assistant erhalten"
        else:
            error = "kein HA-Zugang (SUPERVISOR_TOKEN oder ha_url + ha_token)"
    return words, error


async def build_engine_for(settings: Settings, logger=print) -> tuple[Any, list[str], str | None]:
    """Engine inkl. Hotwords bauen. Gibt (engine, hotwords, fehler) zurueck."""
    words: list[str] = []
    error: str | None = None
    hotwords_file: str | None = None
    if supports_hotwords(settings):
        words, error = await resolve_hotwords(settings, logger)
        # Manuelle Eintraege haben Vorrang; Gesamtzahl begrenzen.
        words = words[:400]
        count = write_hotwords(words, hotwords_path()) if words else 0
        if count:
            hotwords_file = str(hotwords_path())
    engine = await asyncio.to_thread(
        build_engine,
        settings.model,
        settings.model_url or None,
        settings.num_threads,
        settings.model_type or None,
        settings.languages() or None,
        settings.kind or None,
        hotwords_file,
        settings.hotwords_score,
        logger,
    )
    return engine, words, error
