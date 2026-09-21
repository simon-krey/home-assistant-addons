"""sherpa-onnx Streaming-STT fuer den Wyoming-Server."""

from __future__ import annotations

import os
import tarfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import sherpa_onnx

LogFn = Callable[[str], None]


def log(message: str) -> None:
    print(message, flush=True)


MODELS: dict[str, dict] = {
    "de": {
        "label": "Kroko Deutsch (Streaming-Zipformer)",
        "dir": "sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2",
        "model_type": "zipformer2",
        "languages": ["de"],
    },
    "small-en": {
        "label": "English small (20M)",
        "dir": "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17",
        "url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2",
        "model_type": "",
        "languages": ["en"],
    },
}


@dataclass(frozen=True)
class ModelFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path


def default_models_dir() -> Path:
    env = os.getenv("MODELS_DIR")
    if env:
        return Path(env)
    if Path("/data").is_dir():
        return Path("/data/models")
    return Path(__file__).resolve().parent.parent / "models"


def _download(url: str, dest: Path, logger: LogFn = log) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    logger(f"[MODEL] Download: {url}")
    with urllib.request.urlopen(url, timeout=60) as response, open(tmp, "wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        last = time.time()
        while True:
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            done += len(chunk)
            if time.time() - last > 3:
                last = time.time()
                logger(
                    f"[MODEL]   {done / 1e6:.1f}/{total / 1e6:.1f} MB"
                    if total
                    else f"[MODEL]   {done / 1e6:.1f} MB"
                )
    tmp.rename(dest)


def ensure_model(
    model_key: str,
    models_dir: str | Path | None = None,
    model_url: str | None = None,
    logger: LogFn = log,
) -> Path:
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
            tar.extractall(directory, filter="data")
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

    def reset(self) -> None:
        with self._engine.lock:
            self._engine.recognizer.reset(self._stream)

    def finalize(self) -> str:
        with self._engine.lock:
            self._stream.input_finished()
            while self._engine.recognizer.is_ready(self._stream):
                self._engine.recognizer.decode_stream(self._stream)
            return self._engine.recognizer.get_result(self._stream)


class STTEngine:
    def __init__(
        self,
        model_key: str,
        model_dir: str | Path,
        *,
        languages: list[str] | None = None,
        label: str = "",
        sample_rate: int = 16000,
        num_threads: int = 2,
        provider: str = "cpu",
        model_type: str = "",
        logger: LogFn = log,
    ) -> None:
        self.model_key = model_key
        self.model_dir = Path(model_dir)
        self.label = label or model_key
        self.languages = languages or ["de"]
        self.sample_rate = sample_rate
        self.num_threads = num_threads
        self.provider = provider
        self.model_type = model_type
        self.lock = threading.RLock()

        files = find_model_files(self.model_dir)
        logger(f"[STT] {self.label}: encoder={files.encoder.name}")
        logger(
            f"[STT] threads={num_threads} provider={provider} "
            f"model_type={model_type or '(auto)'}"
        )
        started = time.perf_counter()
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(files.tokens),
            encoder=str(files.encoder),
            decoder=str(files.decoder),
            joiner=str(files.joiner),
            num_threads=num_threads,
            sample_rate=sample_rate,
            provider=provider,
            model_type=model_type,
            enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=1.2,
            rule3_min_utterance_length=20.0,
            decoding_method="greedy_search",
        )
        self.load_seconds = time.perf_counter() - started
        logger(f"[STT] Recognizer bereit in {self.load_seconds:.2f}s")

    def create_session(self) -> STTSession:
        with self.lock:
            return STTSession(self, self.recognizer.create_stream())

    def info(self) -> dict:
        return {
            "model": self.model_key,
            "label": self.label,
            "languages": self.languages,
            "model_dir": str(self.model_dir),
            "sample_rate": self.sample_rate,
            "num_threads": self.num_threads,
            "provider": self.provider,
            "sherpa_version": getattr(sherpa_onnx, "__version__", "?"),
            "load_seconds": round(self.load_seconds, 3),
        }


def build_engine(
    model_key: str,
    model_url: str | None = None,
    num_threads: int = 2,
    logger: LogFn = log,
) -> STTEngine:
    spec = MODELS.get(model_key, MODELS["de"])
    model_dir = ensure_model(model_key, model_url=model_url, logger=logger)
    return STTEngine(
        model_key,
        model_dir,
        languages=list(spec.get("languages", ["de"])),
        label=spec["label"],
        num_threads=num_threads,
        model_type=spec.get("model_type", ""),
        logger=logger,
    )
