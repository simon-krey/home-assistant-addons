"""Dateiquelle zum Testen ohne Mikrofon (WAV, 16-bit PCM, mono)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from .base import AudioSource


def _resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Lineare Interpolation – reicht fuer Tests mit WAV-Dateien."""
    if source_rate == target_rate or len(samples) == 0:
        return samples
    duration = len(samples) / source_rate
    target_len = int(round(duration * target_rate))
    source_x = np.linspace(0.0, duration, num=len(samples), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, samples).astype(np.float32)


class FileSource(AudioSource):
    """Liefert die Samples einer WAV-Datei chunkweise.

    Die Chunks kommen so schnell wie möglich (fuer Tests, nicht in Echtzeit).
    Mit ``loop=True`` beginnt die Datei nach dem Ende von vorn.
    """

    def __init__(
        self,
        path: str | Path,
        sample_rate: int = 16000,
        block_ms: int = 100,
        loop: bool = False,
    ) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.block_size = max(1, int(sample_rate * block_ms / 1000))
        self.loop = loop
        self._samples: np.ndarray | None = None
        self._pos = 0

    def _load(self) -> np.ndarray:
        with wave.open(str(self.path), "rb") as wav:
            if wav.getnchannels() != 1:
                raise ValueError(f"{self.path}: nur mono wird unterstuetzt")
            if wav.getsampwidth() != 2:
                raise ValueError(f"{self.path}: nur 16-bit PCM wird unterstuetzt")
            source_rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
        if source_rate != self.sample_rate:
            samples = _resample(samples, source_rate, self.sample_rate)
        return samples

    def start(self) -> None:
        self._samples = self._load()
        self._pos = 0

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        if self._samples is None:
            return None
        if self._pos >= len(self._samples):
            if not self.loop:
                return None
            self._pos = 0
        block = self._samples[self._pos : self._pos + self.block_size]
        self._pos += self.block_size
        return block

    def stop(self) -> None:
        self._samples = None
        self._pos = 0
