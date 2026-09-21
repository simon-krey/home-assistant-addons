"""Verlauf der Conversation-Turns."""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class ConversationHistory:
    def __init__(self, directory: str | Path, limit: int = 200) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.index_file = self.directory / "conversations.json"
        self._lock = threading.RLock()
        self._limit = max(0, limit)
        self._entries: list[dict[str, Any]] = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.index_file.exists():
            return []
        try:
            data = json.loads(self.index_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _persist(self) -> None:
        self.index_file.write_text(
            json.dumps(self._entries, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def configure(self, limit: int) -> None:
        with self._lock:
            self._limit = max(0, limit)
            while len(self._entries) > self._limit:
                self._entries.pop()
            self._persist()

    def add(self, result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            entry = {
                "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": time.time(),
                "text": result.get("_text", ""),
                "response": result.get("response", ""),
                "tool_calls": [
                    {"name": item.get("name"), "arguments": item.get("arguments")}
                    for item in result.get("executed", [])
                ],
                "results": [item.get("result") for item in result.get("executed", [])],
                "refusal": result.get("refusal", False),
                "low_confidence": result.get("low_confidence", False),
                "error": result.get("error", False),
                "error_message": result.get("error_message"),
                "error_traceback": result.get("error_traceback"),
                "fallback_ha": result.get("fallback_ha", False),
                "backend": result.get("backend"),
                "needle_ms": result.get("latency_ms"),
                "dry_run": result.get("dry_run", False),
                "language": result.get("language"),
            }
            self._entries.insert(0, entry)
            while len(self._entries) > self._limit:
                self._entries.pop()
            self._persist()
            return entry

    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries[offset : offset + limit])

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._persist()
