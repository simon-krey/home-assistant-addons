"""Mikrofonquelle auf Basis von ``sounddevice``.

Der Audio-Callback von PortAudio darf nicht blockieren, deshalb schreibt er
die Chunks nur in eine Queue. Der Consumer-Thread ruft ``read()`` auf.
"""

from __future__ import annotations

import queue
import sys

import numpy as np
import sounddevice as sd

from .base import AudioSource


class MicrophoneSource(AudioSource):
    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        block_ms: int = 100,
        device: str | int | None = None,
        max_queue_chunks: int = 100,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.block_size = max(1, int(sample_rate * block_ms / 1000))
        self.device = device
        self._queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=max_queue_chunks)
        self._stream: sd.InputStream | None = None
        self._dropped = 0

    # -- PortAudio callback ------------------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            print(f"[AUDIO] {status}", file=sys.stderr)
        chunk = np.asarray(indata, dtype=np.float32).reshape(-1).copy()
        try:
            self._queue.put_nowait(chunk)
        except queue.Full:
            # Lieber verwerfen als den Callback blockieren.
            self._dropped += 1
            if self._dropped % 100 == 1:
                print(
                    f"[AUDIO] Queue voll, {self._dropped} Chunks verworfen",
                    file=sys.stderr,
                )

    # -- AudioSource -------------------------------------------------------
    def start(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            blocksize=self.block_size,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def read(self, timeout: float | None = None) -> np.ndarray | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def list_input_devices() -> list[tuple[int, str, int]]:
    """Alle Eingabegeraete als ``(index, name, max_input_channels)``."""
    result: list[tuple[int, str, int]] = []
    for index, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            result.append((index, dev["name"], dev["max_input_channels"]))
    return result


def _main() -> None:
    import time

    print("Eingabegeraete:")
    for index, name, channels in list_input_devices():
        print(f"  [{index}] {name} ({channels} ch)")

    source = MicrophoneSource()
    source.start()
    print(f"\nNehme 3 s auf ({source.sample_rate} Hz, block={source.block_size}) ...")
    deadline = time.time() + 3.0
    chunks = 0
    peak = 0.0
    while time.time() < deadline:
        chunk = source.read(timeout=0.5)
        if chunk is None:
            continue
        chunks += 1
        peak = max(peak, float(np.abs(chunk).max()))
        rms = float(np.sqrt(np.mean(chunk**2)))
        print(f"  chunk {chunks:3d}: {chunk.shape[0]:5d} samples  rms={rms:.4f}")
    source.stop()
    print(f"\n{chunks} Chunks, Peak={peak:.4f}")


if __name__ == "__main__":
    _main()
