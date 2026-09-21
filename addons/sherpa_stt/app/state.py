"""Gemeinsamer Laufzeit-Zustand fuer Wyoming-Server und Web-UI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .engine import STTEngine
from .history import HistoryStore
from .settings import Settings


@dataclass
class AppState:
    settings: Settings
    engine: STTEngine
    history: HistoryStore
    lock: threading.RLock = field(default_factory=threading.RLock)
    stats: dict = field(
        default_factory=lambda: {
            "requests": 0,
            "errors": 0,
            "last_text": None,
            "last_at": None,
            "started_at": time.time(),
        }
    )

    def replace_engine(self, engine: STTEngine) -> None:
        with self.lock:
            self.engine = engine

    def current_engine(self) -> STTEngine:
        with self.lock:
            return self.engine
