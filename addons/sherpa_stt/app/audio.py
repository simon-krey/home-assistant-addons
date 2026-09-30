"""Audio-Vorverarbeitung fuer die Spracherkennung.

Die Modelle (Kroko/Parakeet) erwarten Sprache mit gleichmaessigem Pegel. In der
Praxis kommen die Aufnahmen aber mit

* **Gleichspannungsanteil (DC-Offset)**,
* **Brummen/Dröhnen** (50/100 Hz, Luefter, Griffgeraeusche),
* **sehr leisen oder uebersteuerten Pegeln** und
* **Grundrauschen**

an. Die Forschungslage ist bei *aggressiver* Rauschunterdrueckung eindeutig:
sie kann die Erkennung sogar verschlechtern, weil sie Sprachmerkmale entfernt
(Deepgram "Noise Reduction Paradox", mehrere ASR-Studien 2024-2026). Deshalb
setzt dieses Modul auf **konservative, sprachschonende** Verfahren:

==============  ==================================================================
``off``         keine Veraenderung
``light``       DC-Entfernung + 80 Hz-Hochpass + sanfter Limiter (Clipping-Schutz)
``normalize``   ``light`` + AGC/Pegelnormalisierung (leise Aufnahmen anheben)
``full``        ``normalize`` + leises Rauschgate in Sprechpausen
==============  ==================================================================

Alle Filter sind **zustandsbehaftet** und damit fuer Streaming geeignet: jeder
Chunk wird mit demselben Filterzustand verarbeitet, es gibt keine Sprünge an
Chunk-Grenzen. Fuer Offline-Modelle kann zusaetzlich die ganze Aeusserung
normalisiert werden (``process_utterance``).
"""

from __future__ import annotations

import math

import numpy as np

MODES = ("off", "light", "normalize", "full")


# ---------------------------------------------------------------------------
# Filter (RBJ Audio-EQ-Cookbook, Direct Form I, zustandsbehaftet)
# ---------------------------------------------------------------------------
class Biquad:
    def __init__(self, b: tuple[float, float, float], a: tuple[float, float, float]) -> None:
        self.b0, self.b1, self.b2 = b
        self.a0, self.a1, self.a2 = a
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        # Direct Form I: y[n] = b0*x[n] + b1*x[n-1] + b2*x[n-2] - a1*y[n-1] - a2*y[n-2]
        out = np.empty_like(x)
        b0, b1, b2 = self.b0, self.b1, self.b2
        a1, a2 = self.a1, self.a2
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        for i, sample in enumerate(x):
            value = b0 * sample + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1 = x1, sample
            y2, y1 = y1, value
            out[i] = value
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        return out

    def reset(self) -> None:
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0


def highpass_biquad(cutoff: float, sample_rate: int, q: float = 0.707) -> Biquad:
    """Butterworth-Hochpass (2. Ordnung) als Biquad."""
    w0 = 2.0 * math.pi * cutoff / sample_rate
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    b0 = (1.0 + cos_w0) / 2.0
    b1 = -(1.0 + cos_w0)
    b2 = (1.0 + cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return Biquad((b0 / a0, b1 / a0, b2 / a0), (1.0, a1 / a0, a2 / a0))


def lowpass_biquad(cutoff: float, sample_rate: int, q: float = 0.707) -> Biquad:
    w0 = 2.0 * math.pi * cutoff / sample_rate
    cos_w0 = math.cos(w0)
    alpha = math.sin(w0) / (2.0 * q)
    b0 = (1.0 - cos_w0) / 2.0
    b1 = 1.0 - cos_w0
    b2 = (1.0 - cos_w0) / 2.0
    a0 = 1.0 + alpha
    a1 = -2.0 * cos_w0
    a2 = 1.0 - alpha
    return Biquad((b0 / a0, b1 / a0, b2 / a0), (1.0, a1 / a0, a2 / a0))


# ---------------------------------------------------------------------------
# Bausteine
# ---------------------------------------------------------------------------
def soft_limit(x: np.ndarray, ceiling: float = 0.98) -> np.ndarray:
    """Sanftes Begrenzen statt hartem Clipping (tanh-Kennlinie oberhalb des Knies)."""
    if len(x) == 0:
        return x
    peak = float(np.max(np.abs(x)))
    if peak <= ceiling:
        return x
    knee = ceiling * 0.7
    sign = np.sign(x)
    magnitude = np.abs(x)
    compressed = np.where(
        magnitude <= knee,
        magnitude,
        knee + (ceiling - knee) * np.tanh((magnitude - knee) / (ceiling - knee)),
    )
    return (sign * compressed).astype(np.float32)


class DCBlocker:
    """Einpoliger Hochpass, entfernt DC-Offset ohne Sprachanteile zu beruehren."""

    def __init__(self, sample_rate: int, cutoff: float = 20.0) -> None:
        self._pole = math.exp(-2.0 * math.pi * cutoff / sample_rate)
        self._last_x = 0.0
        self._last_y = 0.0

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        pole = self._pole
        last_x, last_y = self._last_x, self._last_y
        for i, sample in enumerate(x):
            value = sample - last_x + pole * last_y
            last_x, last_y = sample, value
            out[i] = value
        self._last_x, self._last_y = last_x, last_y
        return out

    def reset(self) -> None:
        self._last_x = self._last_y = 0.0


class AGC:
    """RMS-basierte Pegelregelung mit Attack/Release und sanftem Limiter.

    Der Pegel wird blockweise (10 ms) gemessen und die Verstaerkung **linear
    interpoliert**, damit keine Sprünge an Blockgrenzen entstehen. Attack
    (lauter werden) ist etwas schneller als Release (leiser werden), damit
    Spracheinsaetze nicht untergehen.
    """

    def __init__(
        self,
        sample_rate: int,
        *,
        target_dbfs: float = -20.0,
        max_gain_db: float = 30.0,
        min_gain_db: float = -24.0,
        attack_ms: float = 15.0,
        release_ms: float = 250.0,
        block_ms: float = 10.0,
    ) -> None:
        self._sr = sample_rate
        self._target = 10.0 ** (target_dbfs / 20.0)
        self._max_gain = 10.0 ** (max_gain_db / 20.0)
        self._min_gain = 10.0 ** (min_gain_db / 20.0)
        self._attack = math.exp(-1.0 / max(1.0, sample_rate * attack_ms / 1000.0))
        self._release = math.exp(-1.0 / max(1.0, sample_rate * release_ms / 1000.0))
        self._block = max(1, int(sample_rate * block_ms / 1000.0))
        self._gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        block = self._block
        gain = self._gain
        for start in range(0, len(x), block):
            chunk = x[start : start + block]
            rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
            if rms > 1e-6:
                desired = self._target / rms
                desired = min(self._max_gain, max(self._min_gain, desired))
            else:
                desired = gain
            coefficient = self._attack if desired > gain else self._release
            # Verstaerkung linear vom aktuellen zum Zielwert ueber den Block
            end_gain = gain + (desired - gain) * (1.0 - coefficient)
            ramp = np.linspace(gain, end_gain, len(chunk), endpoint=False)
            out[start : start + block] = chunk * ramp
            gain = end_gain
        self._gain = gain
        return out

    def reset(self) -> None:
        self._gain = 1.0


class NoiseGate:
    """Sehr konservatives Rauschgate (Downward-Expander).

    Nur wenn der Pegel deutlich unter dem *laufenden* Sprachpegel liegt (echte
    Sprechpause), wird um bis zu ``max_atten_db`` abgesenkt. Waehrend Sprache
    bleibt das Signal unveraendert. Damit wird kein Sprachanteil entfernt – im
    Gegensatz zur spektralen Subtraktion, die laut Studien schaden kann.
    """

    def __init__(
        self,
        sample_rate: int,
        *,
        max_atten_db: float = 18.0,
        threshold_db: float = -48.0,
        block_ms: float = 10.0,
        attack_ms: float = 5.0,
        release_ms: float = 120.0,
    ) -> None:
        self._sr = sample_rate
        self._max_atten = 10.0 ** (max_atten_db / 20.0)
        self._threshold = 10.0 ** (threshold_db / 20.0)
        self._attack = math.exp(-1.0 / max(1.0, sample_rate * attack_ms / 1000.0))
        self._release = math.exp(-1.0 / max(1.0, sample_rate * release_ms / 1000.0))
        self._block = max(1, int(sample_rate * block_ms / 1000.0))
        self._gain = 1.0

    def process(self, x: np.ndarray) -> np.ndarray:
        out = np.empty_like(x)
        block = self._block
        gain = self._gain
        for start in range(0, len(x), block):
            chunk = x[start : start + block]
            rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
            if rms < self._threshold:
                desired = 1.0 / self._max_atten
            else:
                desired = 1.0
            coefficient = self._attack if desired < gain else self._release
            end_gain = gain + (desired - gain) * (1.0 - coefficient)
            ramp = np.linspace(gain, end_gain, len(chunk), endpoint=False)
            out[start : start + block] = chunk * ramp
            gain = end_gain
        self._gain = gain
        return out

    def reset(self) -> None:
        self._gain = 1.0


# ---------------------------------------------------------------------------
# Gesamt-Pipeline
# ---------------------------------------------------------------------------
class AudioPreprocessor:
    """Zustandsbehaftete Vorverarbeitung fuer einen Audio-Stream."""

    def __init__(
        self,
        mode: str = "normalize",
        sample_rate: int = 16000,
        *,
        highpass_hz: float = 80.0,
        target_dbfs: float = -20.0,
        max_gain_db: float = 24.0,
    ) -> None:
        self.mode = mode if mode in MODES else "off"
        self.sample_rate = sample_rate
        self._dc: DCBlocker | None = None
        self._highpass: Biquad | None = None
        self._agc: AGC | None = None
        self._gate: NoiseGate | None = None
        if self.mode != "off":
            self._dc = DCBlocker(sample_rate)
            self._highpass = highpass_biquad(highpass_hz, sample_rate)
        if self.mode in ("normalize", "full"):
            self._agc = AGC(sample_rate, target_dbfs=target_dbfs, max_gain_db=max_gain_db)
        if self.mode == "full":
            self._gate = NoiseGate(sample_rate)

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def process(self, samples: np.ndarray) -> np.ndarray:
        """Einen Chunk verarbeiten (Streaming). Gibt ``float32`` in [-1, 1] zurueck."""
        if not self.enabled or len(samples) == 0:
            return samples
        data = np.asarray(samples, dtype=np.float32)
        if self._dc is not None:
            data = self._dc.process(data)
        if self._highpass is not None:
            data = self._highpass.process(data)
        if self._gate is not None:
            data = self._gate.process(data)
        if self._agc is not None:
            data = self._agc.process(data)
        data = soft_limit(data)
        return np.clip(data, -1.0, 1.0).astype(np.float32)

    def process_utterance(self, samples: np.ndarray) -> np.ndarray:
        """Ganze Aeusserung verarbeiten und abschliessend peak-normalisieren.

        Fuer Offline-Modelle/Selbsttest: nach der zustandsbehafteten Pipeline
        wird die komplette Aufnahme auf knapp unter Vollaussteuerung gebracht.
        """
        data = self.process(samples)
        if not self.enabled or len(data) == 0:
            return data
        peak = float(np.max(np.abs(data)))
        if peak > 1e-6:
            data = (data * (0.97 / peak)).astype(np.float32)
        return np.clip(data, -1.0, 1.0).astype(np.float32)

    def reset(self) -> None:
        for component in (self._dc, self._highpass, self._agc, self._gate):
            if component is not None:
                component.reset()
