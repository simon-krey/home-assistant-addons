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
OPTIONS_FILE = Path(os.getenv("OPTIONS_FILE", "/data/options.json"))
OPTIONS_SNAPSHOT_FILE = DATA_DIR / ".options_snapshot"


@dataclass
class Settings:
    model: str = "de"
    model_url: str = ""
    model_type: str = ""
    kind: str = ""
    language: str = ""
    num_threads: int = int(os.getenv("STT_NUM_THREADS", "2"))
    wyoming_port: int = int(os.getenv("WYOMING_PORT", "10300"))
    web_port: int = int(os.getenv("WEB_PORT", "8000"))
    streaming_transcripts: bool = False
    save_audio: bool = True
    history_limit: int = 100
    zeroconf: str = ""
    debug_logging: bool = False
    audio_preprocessing: str = "normalize"
    # Hotwords / Contextual Biasing (nur Parakeet v3)
    hotwords: str = ""
    hotwords_score: float = 2.5
    hotwords_from_ha: bool = True
    hotwords_domains: str = "light,switch,media_player,scene,climate,fan,input_boolean"
    # Home Assistant (nur ausserhalb von HAOS noetig)
    ha_url: str = os.getenv("HA_URL", "")
    ha_token: str = os.getenv("HA_TOKEN", "")
    # faster-whisper (CTranslate2)
    faster_whisper_compute_type: str = "int8"
    faster_whisper_beam_size: int = 1

    def languages(self) -> list[str]:
        return [part.strip() for part in self.language.split(",") if part.strip()]

    def hotwords_domains_list(self) -> list[str]:
        return [part.strip() for part in self.hotwords_domains.split(",") if part.strip()]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Settings":
        known = set(cls.__dataclass_fields__)
        clean = {key: value for key, value in (data or {}).items() if key in known}
        # Leere Strings fuer Zahlen/Strings normalisieren
        return cls(**clean)


def _read_options() -> dict:
    if not OPTIONS_FILE.exists():
        return {}
    try:
        data = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_settings() -> Settings:
    """Einstellungen laden und Add-on-Optionen-Aenderungen uebernehmen.

    Die Web-UI schreibt nach ``settings.json`` (hat Vorrang). Wird jedoch eine
    Option im HA-Konfigurationstab geaendert, schreibt der Supervisor
    ``options.json`` und startet das Add-on neu. Damit diese Aenderung nicht
    verloren geht, wird sie erkannt (Vergleich mit dem letzten Stand) und nur
    die tatsaechlich geaenderten Felder uebernommen.
    """
    options = _read_options()
    current_snapshot = json.dumps(options, sort_keys=True)
    previous_snapshot: str | None = None
    if OPTIONS_SNAPSHOT_FILE.exists():
        try:
            previous_snapshot = OPTIONS_SNAPSHOT_FILE.read_text(encoding="utf-8")
        except OSError:
            previous_snapshot = None

    settings: Settings | None = None
    if SETTINGS_FILE.exists():
        try:
            settings = Settings.from_dict(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError):
            settings = None
    if settings is None:
        settings = Settings.from_dict(options)

    known = set(Settings.__dataclass_fields__)
    defaults = Settings()
    if options:
        if previous_snapshot is None:
            # Erstlauf: nur explizit geaenderte, nicht-standard Optionen uebernehmen
            for key, value in options.items():
                if key not in known:
                    continue
                if value == getattr(defaults, key):
                    continue
                if value == getattr(settings, key):
                    continue
                setattr(settings, key, value)
        else:
            try:
                previous = json.loads(previous_snapshot)
            except json.JSONDecodeError:
                previous = {}
            for key, value in options.items():
                if key in known and previous.get(key) != value:
                    setattr(settings, key, value)

    post_init = getattr(settings, "__post_init__", None)
    if callable(post_init):
        post_init()
    save_settings(settings)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        OPTIONS_SNAPSHOT_FILE.write_text(current_snapshot, encoding="utf-8")
    except OSError:
        pass
    return settings


def save_settings(settings: Settings) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def reset_to_addon_options() -> Settings:
    settings = Settings.from_dict(_read_options())
    save_settings(settings)
    return settings
