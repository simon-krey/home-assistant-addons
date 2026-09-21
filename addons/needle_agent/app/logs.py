"""Log-Ringpuffer + optionaler stdout/stderr-Abfang.

Die Aufzeichnung ist zur Laufzeit schaltbar (``log_capture``), damit sich das
Add-on bei Bedarf still verhaelt. ``/api/logs`` zeigt die letzten Zeilen.
"""

from __future__ import annotations

import collections
import logging
import sys
import threading
import time
from typing import Any


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 600) -> None:
        super().__init__()
        self._buffer: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.enabled = True

    def add_line(self, message: str, level: str = "APP") -> None:
        if not self.enabled:
            return
        entry = {"time": time.strftime("%H:%M:%S"), "level": level, "message": message}
        with self._lock:
            self._buffer.append(entry)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.enabled:
            return
        try:
            message = self.format(record)
        except Exception:  # noqa: BLE001
            return
        entry = {
            "time": time.strftime("%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        with self._lock:
            self._buffer.append(entry)

    def entries(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._buffer)[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()


class _TeeStream:
    """Schreibt weiter auf den Originalstream und puffert Zeilen."""

    def __init__(self, original: Any, handler: RingBufferHandler, level: str) -> None:
        self.original = original
        self._handler = handler
        self._level = level
        self._pending = ""

    def write(self, data: str) -> int:
        written = self.original.write(data)
        self._pending += data
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._handler.add_line(line.rstrip(), self._level)
        return written

    def flush(self) -> None:
        self.original.flush()

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self.original.fileno()


LOG_BUFFER = RingBufferHandler()


def set_enabled(enabled: bool) -> None:
    """Aufzeichnung an/aus (Ringpuffer + Stream-Abfang)."""
    LOG_BUFFER.enabled = bool(enabled)
    set_capture(bool(enabled))


def is_enabled() -> bool:
    return bool(LOG_BUFFER.enabled)


def set_capture(enabled: bool) -> None:
    """stdout/stderr abfangen bzw. wieder freigeben."""
    if enabled:
        if not isinstance(sys.stdout, _TeeStream):
            sys.stdout = _TeeStream(sys.stdout, LOG_BUFFER, "STDOUT")
        if not isinstance(sys.stderr, _TeeStream):
            sys.stderr = _TeeStream(sys.stderr, LOG_BUFFER, "STDERR")
    else:
        if isinstance(sys.stdout, _TeeStream):
            sys.stdout = sys.stdout.original
        if isinstance(sys.stderr, _TeeStream):
            sys.stderr = sys.stderr.original


def set_level(level: int) -> None:
    LOG_BUFFER.setLevel(level)
    logging.getLogger().setLevel(level)


def install(level: int = logging.INFO, capture: bool = True) -> None:
    LOG_BUFFER.setLevel(level)
    LOG_BUFFER.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    if LOG_BUFFER not in root.handlers:
        root.addHandler(LOG_BUFFER)
    LOG_BUFFER.enabled = True
    set_capture(capture)


def log(message: str, level: str = "APP") -> None:
    """Ersatz fuer ``print``, der zusaetzlich in den Ringpuffer schreibt."""
    print(message, flush=True)
    LOG_BUFFER.add_line(message, level)
