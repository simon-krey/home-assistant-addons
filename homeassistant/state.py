"""Thread-sicherer State-Cache.

Wird beim Start gefuellt und danach ueber ``state_changed``-Events aktuell
gehalten, damit nicht jede Anfrage einen neuen Request an Home Assistant
ausloest.
"""

from __future__ import annotations

import threading
from typing import Any


class StateCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, Any]] = {}

    def update(self, entity_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._states[entity_id] = state

    def update_many(self, states: dict[str, dict[str, Any]]) -> None:
        with self._lock:
            self._states.update(states)

    def apply_state_changed(self, event: dict[str, Any]) -> None:
        """``state_changed``-Event aus Home Assistant einarbeiten."""
        data = event.get("data", event)
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        if not entity_id or new_state is None:
            return
        with self._lock:
            self._states[entity_id] = new_state

    def get(self, entity_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._states.get(entity_id)

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._states)

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
