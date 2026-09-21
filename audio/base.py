"""Audioquellen.

Das Modul kennt weder STT noch Needle. Es liefert nur PCM-Samples.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class AudioSource(ABC):
    """Abstrakte Audioquelle.

    ``read()`` blockiert bis ein Chunk vorliegt oder liefert ``None`` bei
    Timeout/Stopp. Die Samples sind ``float32`` in ``[-1, 1]``, mono.
    """

    sample_rate: int

    @abstractmethod
    def start(self) -> None:
        """Quelle öffnen und Aufnahme starten."""

    @abstractmethod
    def read(self, timeout: float | None = None) -> np.ndarray | None:
        """Nächsten Audiochunk liefern oder ``None`` bei Timeout."""

    @abstractmethod
    def stop(self) -> None:
        """Aufnahme stoppen und Ressourcen freigeben."""

    def __enter__(self) -> "AudioSource":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
