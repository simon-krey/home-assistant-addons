"""Gemeinsamer Laufzeit-Zustand."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .agent import ConversationEngine
from .entities import EntityInfo
from .ha_client import HomeAssistantClient
from .history import ConversationHistory
from .settings import Settings
from .tools import ToolSet, build_toolset


@dataclass
class AppState:
    settings: Settings
    ha: HomeAssistantClient
    history: ConversationHistory
    tool_index_path: str = ""
    lock: threading.RLock = field(default_factory=threading.RLock)
    entities: list[EntityInfo] = field(default_factory=list)
    toolset: ToolSet | None = None
    engine: ConversationEngine | None = None
    stats: dict = field(
        default_factory=lambda: {
            "requests": 0,
            "errors": 0,
            "last_text": None,
            "last_response": None,
            "last_at": None,
            "started_at": time.time(),
            "refreshes": 0,
            "last_refresh": None,
        }
    )

    def build(self) -> None:
        with self.lock:
            self.toolset = build_toolset(
                self.entities, self.settings.domains_list(), self.settings.tools_list()
            )
            self.engine = ConversationEngine(
                self.settings,
                self.ha,
                self.toolset,
                self.history,
                tool_index_path=self.tool_index_path or None,
            )
            self.stats["last_refresh"] = time.time()

    def reconfigure(self) -> None:
        with self.lock:
            toolset = build_toolset(
                self.entities, self.settings.domains_list(), self.settings.tools_list()
            )
            if self.engine is None:
                self.toolset = toolset
                self.engine = ConversationEngine(
                    self.settings, self.ha, toolset, self.history,
                    tool_index_path=self.tool_index_path or None,
                )
            else:
                self.toolset = toolset
                self.engine.reconfigure(self.settings, toolset)

    def current_engine(self) -> ConversationEngine:
        with self.lock:
            assert self.engine is not None
            return self.engine

    def current_toolset(self) -> ToolSet:
        with self.lock:
            assert self.toolset is not None
            return self.toolset
