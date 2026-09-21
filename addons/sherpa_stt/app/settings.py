"""Persistente Einstellungen.

Reihenfolge: ``/data/settings.json`` (aus der Web-UI) hat Vorrang. Existiert
die Datei noch nicht, werden die Add-on-Optionen aus ``/data/options.json``
als Startwerte uebernommen.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def data_dir() -> Path:
    env = os.getenv("DATA_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data")
    return Path(__file__).resolve().parent.parent / "data"


DATA_DIR = data_dir()
SETTINGS_FILE = DATA_DIR / "settings.json"
OPTIONS_FILE = Path("/data/options.json")


@dataclass
class Settings:
    model: str = "de"
    model_url: str = ""
    language: str = ""
    num_threads: int = int(os.getenv("STT_NUM_THREADS", "2"))
    wyoming_port: int = int(os.getenv("WYOMING_PORT", "10300"))
    web_port: int = int(os.getenv("WEB_PORT", "8000"))
    streaming_transcripts: bool = False
    save_audio: bool = True
    history_limit: int = 100
    zeroconf: str = ""
    debug_logging: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Settings":
        known = set(cls.__dataclass_fields__)
        clean = {key: value for key, value in (data or {}).items() if key in known}
        # Leere Strings fuer Zahlen/Strings normalisieren
        return cls(**clean)


def load_settings() -> Settings:
    if SETTINGS_FILE.exists():
        try:
            return Settings.from_dict(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    options: dict = {}
    if OPTIONS_FILE.exists():
        try:
            options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            options = {}
    settings = Settings.from_dict(options)
    save_settings(settings)
    return settings


def save_settings(settings: Settings) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def reset_to_addon_options() -> Settings:
    options: dict = {}
    if OPTIONS_FILE.exists():
        try:
            options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            options = {}
    settings = Settings.from_dict(options)
    save_settings(settings)
    return settings
