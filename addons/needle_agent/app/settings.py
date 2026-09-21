"""Persistente Einstellungen (Web-UI hat Vorrang vor Add-on-Optionen)."""

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
    dry_run: bool = True
    domains: str = "light,switch,media_player"
    tools: str = "turn_on_light,turn_off_light,turn_on_switch,turn_off_switch,get_entity_state"
    max_steps: int = 4
    language: str = "de"
    system: str = ""
    refresh_seconds: int = 300
    debug_logging: bool = False
    ha_url: str = os.getenv("HA_URL", "")
    ha_token: str = os.getenv("HA_TOKEN", "")
    response_templates: str = ""  # JSON-Objekt, leer = Defaults
    wyoming_port: int = int(os.getenv("WYOMING_PORT", "10300"))
    web_port: int = int(os.getenv("WEB_PORT", "8000"))
    history_limit: int = 200

    def domains_list(self) -> list[str]:
        return [part.strip() for part in self.domains.split(",") if part.strip()]

    def tools_list(self) -> list[str]:
        return [part.strip() for part in self.tools.split(",") if part.strip()]

    def templates(self) -> dict[str, str]:
        from .responses import DEFAULT_TEMPLATES

        merged = dict(DEFAULT_TEMPLATES)
        if self.response_templates.strip():
            try:
                custom = json.loads(self.response_templates)
                if isinstance(custom, dict):
                    merged.update({str(k): str(v) for k, v in custom.items()})
            except json.JSONDecodeError:
                pass
        return merged

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict | None) -> "Settings":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


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
