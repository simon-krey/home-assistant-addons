"""Minimaler thread-sicherer Event-Bus fuer Terminal und Web-UI."""

from __future__ import annotations

import queue
import threading
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._lock = threading.Lock()

    def subscribe(self, maxsize: int = 2000) -> "queue.Queue[dict[str, Any]]":
        channel: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: "queue.Queue[dict[str, Any]]") -> None:
        with self._lock:
            self._subscribers.discard(channel)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for channel in subscribers:
            try:
                channel.put_nowait(event)
            except queue.Full:
                pass
