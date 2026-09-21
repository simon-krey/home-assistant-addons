"""Gemeinsamer Laufzeit-Zustand."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .agent import ConversationEngine
from .entities import EntityInfo
from .ha_client import HomeAssistantClient
from .history import ConversationHistory
from .matching import HomeContext, Resolver
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
    context: HomeContext = field(default_factory=HomeContext)
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

    def _make_resolver(self) -> Resolver:
        settings = self.settings
        return Resolver(
            self.entities,
            self.context,
            min_score=settings.resolve_min_score,
            min_margin=settings.resolve_min_margin,
            floor=settings.resolve_floor,
            tool_min_score=settings.tool_match_min_score,
        )

    def build(self) -> None:
        with self.lock:
            self.toolset = build_toolset(
                self.entities,
                self.settings.domains_list(),
                self.settings.tools_list(),
                resolver=self._make_resolver(),
            )
            try:
                self.engine = ConversationEngine(
                    self.settings,
                    self.ha,
                    self.toolset,
                    self.history,
                    tool_index_path=self.tool_index_path or None,
                )
            except Exception as exc:  # noqa: BLE001
                message = f"{type(exc).__name__}: {exc}"
                print(
                    f"[BACKEND ERROR] Initialisierung fehlgeschlagen: {message}\n"
                    "                 -> Fallback auf Backend 'ha'",
                    flush=True,
                )
                self.stats["last_error"] = message
                self.engine = ConversationEngine(
                    self.settings,
                    self.ha,
                    self.toolset,
                    self.history,
                    tool_index_path=self.tool_index_path or None,
                    force_backend="ha",
                )
            self.stats["last_refresh"] = time.time()

    def reconfigure(self) -> None:
        with self.lock:
            toolset = build_toolset(
                self.entities,
                self.settings.domains_list(),
                self.settings.tools_list(),
                resolver=self._make_resolver(),
            )
            self.toolset = toolset
            try:
                if self.engine is None:
                    self.engine = ConversationEngine(
                        self.settings, self.ha, toolset, self.history,
                        tool_index_path=self.tool_index_path or None,
                    )
                else:
                    self.engine.reconfigure(self.settings, toolset)
            except Exception as exc:  # noqa: BLE001
                message = f"{type(exc).__name__}: {exc}"
                print(f"[BACKEND ERROR] Neuaufbau fehlgeschlagen: {message}", flush=True)
                self.stats["last_error"] = message

    def current_engine(self) -> ConversationEngine:
        with self.lock:
            assert self.engine is not None
            return self.engine

    def current_toolset(self) -> ToolSet:
        with self.lock:
            assert self.toolset is not None
            return self.toolset
