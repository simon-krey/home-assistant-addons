"""sherpa-onnx Streaming-STT fuer den Pi-Realtime-Test.

Enthaelt Modell-Discovery, automatischen Download und eine duenne
Session-Abstraktion. Mehrere Sessions teilen sich einen Recognizer, die
Dekodierung wird per Lock serialisiert.
"""

from __future__ import annotations

import os
import tarfile
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import sherpa_onnx

LogFn = Callable[[str], None]


def log(message: str) -> None:
    print(message, flush=True)


MODELS: dict[str, dict[str, str]] = {
    "de": {
        "label": "Deutsch (Kroko, fp32, 70 MB Encoder)",
        "dir": "sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2",
        "model_type": "zipformer2",
    },
    "small-en": {
        "label": "English small (20M, schnell)",
        "dir": "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2",
        "model_type": "",
    },
}


@dataclass(frozen=True)
class ModelFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path


@dataclass
class EngineInfo:
    model_dir: str
    model_type: str
    encoder: str
    sample_rate: int
    num_threads: int
    provider: str
    sherpa_version: str
    label: str = ""
    extra: dict = field(default_factory=dict)


def default_models_dir() -> Path:
    env = os.getenv("MODELS_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/models")
    return Path(__file__).resolve().parent / "models"


def _download(url: str, dest: Path, logger: LogFn = log) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    logger(f"[MODEL] Download: {url}")
    with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last_report = time.time()
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if time.time() - last_report > 2:
                last_report = time.time()
                if total:
                    logger(f"[MODEL]   {done / 1e6:.1f}/{total / 1e6:.1f} MB")
                else:
                    logger(f"[MODEL]   {done / 1e6:.1f} MB")
    tmp.rename(dest)


def ensure_model(
    model_key: str = "de",
    models_dir: str | Path | None = None,
    model_url: str | None = None,
    logger: LogFn = log,
) -> Path:
    """Modellverzeichnis sicherstellen; bei Bedarf herunterladen und entpacken."""
    directory = Path(models_dir) if models_dir else default_models_dir()
    directory.mkdir(parents=True, exist_ok=True)

    spec = MODELS.get(model_key, MODELS["de"])
    target = directory / spec["dir"]
    if (target / "tokens.txt").exists():
        return target

    url = model_url or spec["url"]
    archive = directory / Path(url).name
    logger(f"[MODEL] {spec['label']}")
    if not archive.exists():
        _download(url, archive, logger)

    logger(f"[MODEL] Entpacke {archive.name} ...")
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(directory, filter="data")  # Python >= 3.12
        except TypeError:
            tar.extractall(directory)

    if (target / "tokens.txt").exists():
        return target
    for candidate in sorted(directory.iterdir()):
        if candidate.is_dir() and (candidate / "tokens.txt").exists():
            return candidate
    raise RuntimeError(f"Modell konnte nicht entpackt werden: {target}")


def _pick(model_dir: Path, stem: str) -> Path:
    candidates = sorted(model_dir.glob(f"{stem}*.onnx"))
    if not candidates:
        raise FileNotFoundError(f"Keine {stem}*.onnx in {model_dir}")
    int8 = [c for c in candidates if ".int8." in c.name]
    non_int8 = [c for c in candidates if ".int8." not in c.name]
    return (int8 or non_int8 or candidates)[0]


def find_model_files(model_dir: str | Path) -> ModelFiles:
    model_dir = Path(model_dir)
    tokens = model_dir / "tokens.txt"
    if not tokens.exists():
        raise FileNotFoundError(f"tokens.txt fehlt in {model_dir}")
    return ModelFiles(
        encoder=_pick(model_dir, "encoder"),
        decoder=_pick(model_dir, "decoder"),
        joiner=_pick(model_dir, "joiner"),
        tokens=tokens,
    )


class STTSession:
    def __init__(self, engine: "STTEngine", stream) -> None:
        self._engine = engine
        self._stream = stream
        self._last_text = ""

    def accept(self, samples: np.ndarray) -> None:
        engine = self._engine
        samples = np.asarray(samples, dtype=np.float32)
        with engine.lock:
            self._stream.accept_waveform(engine.sample_rate, samples)
            while engine.recognizer.is_ready(self._stream):
                engine.recognizer.decode_stream(self._stream)

    def result(self) -> str:
        with self._engine.lock:
            return self._engine.recognizer.get_result(self._stream)

    def endpoint(self) -> bool:
        with self._engine.lock:
            return self._engine.recognizer.is_endpoint(self._stream)

    def reset(self) -> None:
        with self._engine.lock:
            self._engine.recognizer.reset(self._stream)
        self._last_text = ""

    def finalize(self) -> str:
        with self._engine.lock:
            self._stream.input_finished()
            while self._engine.recognizer.is_ready(self._stream):
                self._engine.recognizer.decode_stream(self._stream)
            return self._engine.recognizer.get_result(self._stream)


class STTEngine:
    def __init__(
        self,
        model_dir: str | Path,
        *,
        sample_rate: int = 16000,
        num_threads: int | None = None,
        provider: str | None = None,
        model_type: str | None = None,
        enable_endpoint: bool = True,
        rule1_min_trailing_silence: float = 2.4,
        rule2_min_trailing_silence: float = 1.2,
        rule3_min_utterance_length: float = 20.0,
        label: str = "",
        logger: LogFn = log,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.sample_rate = sample_rate
        self.num_threads = num_threads or int(os.getenv("STT_NUM_THREADS", "2"))
        self.provider = provider or os.getenv("STT_PROVIDER", "cpu")
        self.model_type = model_type if model_type is not None else os.getenv("STT_MODEL_TYPE", "")
        self.lock = threading.RLock()

        files = find_model_files(self.model_dir)
        logger(f"[STT] encoder={files.encoder.name}")
        logger(f"[STT] decoder={files.decoder.name}")
        logger(f"[STT] joiner={files.joiner.name}")
        logger(f"[STT] threads={self.num_threads} provider={self.provider} model_type={self.model_type or '(auto)'}")

        started = time.perf_counter()
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(files.tokens),
            encoder=str(files.encoder),
            decoder=str(files.decoder),
            joiner=str(files.joiner),
            num_threads=self.num_threads,
            sample_rate=sample_rate,
            provider=self.provider,
            model_type=self.model_type,
            enable_endpoint_detection=enable_endpoint,
            rule1_min_trailing_silence=rule1_min_trailing_silence,
            rule2_min_trailing_silence=rule2_min_trailing_silence,
            rule3_min_utterance_length=rule3_min_utterance_length,
            decoding_method="greedy_search",
        )
        self.load_seconds = time.perf_counter() - started
        self.label = label
        self.info = EngineInfo(
            model_dir=str(self.model_dir),
            model_type=self.model_type or "(auto)",
            encoder=files.encoder.name,
            sample_rate=sample_rate,
            num_threads=self.num_threads,
            provider=self.provider,
            sherpa_version=getattr(sherpa_onnx, "__version__", "?"),
            label=label,
            extra={"load_seconds": round(self.load_seconds, 3)},
        )
        logger(f"[STT] Recognizer bereit in {self.load_seconds:.2f}s")

    def create_session(self) -> STTSession:
        with self.lock:
            return STTSession(self, self.recognizer.create_stream())


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or len(samples) == 0:
        return samples.astype(np.float32)
    duration = len(samples) / source_rate
    target_len = int(round(duration * target_rate))
    source_x = np.linspace(0.0, duration, num=len(samples), endpoint=False)
    target_x = np.linspace(0.0, duration, num=target_len, endpoint=False)
    return np.interp(target_x, source_x, samples).astype(np.float32)


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """16-bit PCM WAV aus Bytes lesen, mono machen."""
    import io
    import wave

    with wave.open(io.BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"nur 16-bit PCM unterstuetzt (ist {width * 8} bit)")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def benchmark(
    engine: STTEngine,
    samples: np.ndarray,
    *,
    realtime: bool = False,
    block_ms: int = 100,
    logger: LogFn = log,
) -> dict:
    """RTF messen: process_seconds / audio_seconds.

    ``realtime=True`` speist die Chunks im Aufnahme-Tempo ein und misst, ob
    die Dekodierung hinterherkommt (Lag in ms).
    """
    session = engine.create_session()
    block = max(1, int(engine.sample_rate * block_ms / 1000))
    audio_seconds = len(samples) / engine.sample_rate
    process_seconds = 0.0
    max_lag_ms = 0.0
    wall_start = time.perf_counter()

    for offset in range(0, len(samples), block):
        chunk = samples[offset : offset + block]
        if realtime:
            target = (offset + len(chunk)) / engine.sample_rate
            elapsed = time.perf_counter() - wall_start
            if elapsed < target:
                time.sleep(target - elapsed)
        started = time.perf_counter()
        session.accept(chunk)
        process_seconds += time.perf_counter() - started
        if realtime:
            elapsed = time.perf_counter() - wall_start
            audio_done = (offset + len(chunk)) / engine.sample_rate
            max_lag_ms = max(max_lag_ms, (elapsed - audio_done) * 1000.0)

    started = time.perf_counter()
    text = session.finalize()
    process_seconds += time.perf_counter() - started
    wall_seconds = time.perf_counter() - wall_start

    rtf = process_seconds / audio_seconds if audio_seconds else 0.0
    return {
        "text": text.strip(),
        "audio_seconds": round(audio_seconds, 3),
        "process_seconds": round(process_seconds, 3),
        "wall_seconds": round(wall_seconds, 3),
        "rtf": round(rtf, 4),
        "realtime": rtf < 1.0,
        "max_lag_ms": round(max_lag_ms, 1) if realtime else None,
        "speedup": round(1.0 / rtf, 2) if rtf else None,
    }
