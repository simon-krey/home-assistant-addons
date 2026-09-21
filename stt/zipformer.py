"""Streaming-STT mit sherpa-onnx (Online-Transducer / Zipformer).

Kapselt den Recognizer und genau einen laufenden Recognition-Stream.
Liefert Partial-Ergebnisse waehrend des Sprechens und ein Final-Ergebnis,
sobald sherpa-onnx einen Endpoint (Sprechpause) erkennt.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sherpa_onnx

import config


@dataclass(frozen=True)
class ModelFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path


def _pick(model_dir: Path, stem: str, *, prefer_int8: bool = True) -> Path:
    candidates = sorted(model_dir.glob(f"{stem}*.onnx"))
    if not candidates:
        raise FileNotFoundError(f"Keine Datei {stem}*.onnx in {model_dir}")
    int8 = [c for c in candidates if ".int8." in c.name]
    if prefer_int8 and int8:
        return int8[0]
    non_int8 = [c for c in candidates if ".int8." not in c.name]
    return (non_int8 or candidates)[0]


def find_model_files(model_dir: str | Path = config.STT_MODEL_DIR) -> ModelFiles:
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise FileNotFoundError(f"STT-Modellverzeichnis fehlt: {model_dir}")
    tokens = model_dir / "tokens.txt"
    if not tokens.exists():
        raise FileNotFoundError(f"tokens.txt fehlt in {model_dir}")
    return ModelFiles(
        encoder=_pick(model_dir, "encoder"),
        decoder=_pick(model_dir, "decoder"),
        joiner=_pick(model_dir, "joiner"),
        tokens=tokens,
    )


class StreamingSTT:
    def __init__(
        self,
        model_dir: str | Path = config.STT_MODEL_DIR,
        sample_rate: int = config.SAMPLE_RATE,
        num_threads: int = config.STT_NUM_THREADS,
        provider: str = config.STT_PROVIDER,
        enable_endpoint: bool = config.STT_ENABLE_ENDPOINT,
        model_type: str = config.STT_MODEL_TYPE,
        rule1_min_trailing_silence: float = config.STT_RULE1_TRAILING_SILENCE,
        rule2_min_trailing_silence: float = config.STT_RULE2_TRAILING_SILENCE,
        rule3_min_utterance_length: float = config.STT_RULE3_MIN_UTTERANCE_LENGTH,
    ) -> None:
        self.sample_rate = sample_rate
        self.model_files = find_model_files(model_dir)
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(self.model_files.tokens),
            encoder=str(self.model_files.encoder),
            decoder=str(self.model_files.decoder),
            joiner=str(self.model_files.joiner),
            num_threads=num_threads,
            sample_rate=sample_rate,
            provider=provider,
            model_type=model_type,
            enable_endpoint_detection=enable_endpoint,
            rule1_min_trailing_silence=rule1_min_trailing_silence,
            rule2_min_trailing_silence=rule2_min_trailing_silence,
            rule3_min_utterance_length=rule3_min_utterance_length,
            decoding_method="greedy_search",
        )
        self.stream = self.recognizer.create_stream()

    # -- Interface aus dem Plan -------------------------------------------
    def accept_audio(self, samples: np.ndarray) -> None:
        """Chunk einspeisen und so weit wie moeglich dekodieren."""
        self.stream.accept_waveform(self.sample_rate, np.asarray(samples, dtype=np.float32))
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

    def get_result(self) -> str:
        return self.recognizer.get_result(self.stream)

    def is_endpoint(self) -> bool:
        return self.recognizer.is_endpoint(self.stream)

    def reset(self) -> None:
        self.recognizer.reset(self.stream)

    def finalize(self) -> str:
        """Restliche Samples auswerten (z. B. am Ende einer Aufnahme)."""
        self.stream.input_finished()
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        return self.get_result()


def transcribe_file(path: str | Path, stt: StreamingSTT | None = None) -> tuple[str, list[str]]:
    """WAV dekodieren und alle Zwischenergebnisse zurueckgeben (fuer Tests)."""
    from audio.file import FileSource

    stt = stt or StreamingSTT()
    source = FileSource(path, sample_rate=stt.sample_rate, block_ms=100)
    source.start()

    partials: list[str] = []
    while True:
        chunk = source.read()
        if chunk is None:
            break
        stt.accept_audio(chunk)
        text = stt.get_result()
        if text and (not partials or partials[-1] != text):
            partials.append(text)
        if stt.is_endpoint():
            stt.reset()
    final = stt.finalize()
    if final and (not partials or partials[-1] != final):
        partials.append(final)
    return final, partials


def _main() -> None:
    import time

    wavs = sorted((config.STT_MODEL_DIR / "test_wavs").glob("*.wav"))
    if not wavs:
        print(f"Keine Test-WAVs in {config.STT_MODEL_DIR / 'test_wavs'}")
        return
    print(f"Modell: {config.STT_MODEL_DIR}")
    files = find_model_files()
    print(f"  encoder={files.encoder.name} decoder={files.decoder.name} joiner={files.joiner.name}")
    t0 = time.perf_counter()
    stt = StreamingSTT()
    print(f"Recognizer geladen in {time.perf_counter() - t0:.2f}s")
    for wav in wavs:
        t0 = time.perf_counter()
        final, partials = transcribe_file(wav, stt)
        dt = time.perf_counter() - t0
        print(f"\n{wav.name}:")
        for p in partials:
            marker = "final" if p == final else "partial"
            print(f"  [{marker:7}] {p}")
        print(f"  ({dt:.2f}s)")


if __name__ == "__main__":
    sys.exit(_main())
