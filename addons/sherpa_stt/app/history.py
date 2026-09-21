"""Verlauf: Transkripte inklusive Audio-Sample als WAV."""

from __future__ import annotations

import json
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any


class HistoryStore:
    def __init__(
        self,
        directory: str | Path,
        *,
        limit: int = 100,
        save_audio: bool = True,
    ) -> None:
        self.directory = Path(directory)
        self.audio_dir = self.directory / "audio"
        self.index_file = self.directory / "index.json"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._limit = max(0, limit)
        self._save_audio = save_audio
        self._entries: list[dict[str, Any]] = self._load()

    # -- Persistenz --------------------------------------------------------
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

    # -- Konfiguration -----------------------------------------------------
    def configure(self, limit: int, save_audio: bool) -> None:
        with self._lock:
            self._limit = max(0, limit)
            self._save_audio = save_audio
            self._trim()
            self._persist()

    # -- Schreiben ---------------------------------------------------------
    def add(
        self,
        *,
        text: str,
        language: str | None = None,
        audio_pcm: bytes = b"",
        rate: int = 16000,
        width: int = 2,
        channels: int = 1,
        duration: float = 0.0,
        process_seconds: float = 0.0,
        rtf: float = 0.0,
        source: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            entry_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
            has_audio = False
            if self._save_audio and audio_pcm and duration > 0:
                try:
                    self._write_wav(entry_id, audio_pcm, rate, width, channels)
                    has_audio = True
                except OSError:
                    has_audio = False

            entry = {
                "id": entry_id,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "epoch": time.time(),
                "text": text,
                "language": language,
                "duration": round(duration, 2),
                "process_seconds": round(process_seconds, 3),
                "rtf": round(rtf, 4),
                "source": source,
                "has_audio": has_audio,
            }
            self._entries.insert(0, entry)
            self._trim()
            self._persist()
            return entry

    def _write_wav(
        self, entry_id: str, pcm: bytes, rate: int, width: int, channels: int
    ) -> None:
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self.audio_dir / f"{entry_id}.wav"), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(width)
            wav.setframerate(rate)
            wav.writeframes(pcm)

    def _trim(self) -> None:
        while len(self._entries) > self._limit:
            entry = self._entries.pop()
            self._delete_audio(entry.get("id", ""))

    def _delete_audio(self, entry_id: str) -> None:
        if not entry_id:
            return
        path = self.audio_dir / f"{entry_id}.wav"
        if path.exists():
            path.unlink(missing_ok=True)

    # -- Lesen -------------------------------------------------------------
    def list(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._entries[offset : offset + limit])

    def get(self, entry_id: str) -> dict[str, Any] | None:
        with self._lock:
            return next((e for e in self._entries if e["id"] == entry_id), None)

    def audio_path(self, entry_id: str) -> Path | None:
        path = self.audio_dir / f"{entry_id}.wav"
        return path if path.exists() else None

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    # -- Loeschen ----------------------------------------------------------
    def delete(self, entry_id: str) -> bool:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e["id"] != entry_id]
            self._delete_audio(entry_id)
            changed = len(self._entries) != before
            if changed:
                self._persist()
            return changed

    def clear(self) -> None:
        with self._lock:
            for entry in self._entries:
                self._delete_audio(entry.get("id", ""))
            self._entries = []
            self._persist()
