"""sherpa-onnx Streaming-STT fuer den Wyoming-Server.

Unterstuetzte Modelle sind **Streaming-Zipformer-Transducer** mit
``encoder*.onnx``, ``decoder*.onnx``, ``joiner*.onnx`` und ``tokens.txt``.
Die *-ctc-* Streaming-Modelle funktionieren NICHT (kein Transducer).

``model_type`` bleibt standardmaessig leer (sherpa-onnx erkennt die
Architektur automatisch) und kann bei Bedarf ueberschrieben werden.
"""

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

_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


def log(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    url: str
    languages: tuple[str, ...]
    model_type: str = ""
    dir: str = ""


def _url(name: str) -> str:
    return _RELEASE + name


# Kuratierte Streaming-Zipformer (Transducer). Alle inkl. tokens.txt.
MODELS: dict[str, ModelSpec] = {
    "de": ModelSpec(
        "de", "Deutsch – Kroko (beste DE-Qualität)", _url("sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2"), ("de",)
    ),
    "en-kroko": ModelSpec(
        "en-kroko", "English – Kroko (Groß/Kleinschreibung + Satzzeichen)", _url("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06.tar.bz2"), ("en",)
    ),
    "en-20M": ModelSpec(
        "en-20M", "English – 20M (klein, älter, schwächer)", _url("sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2"), ("en",)
    ),
    "es-kroko": ModelSpec(
        "es-kroko", "Spanisch – Kroko", _url("sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06.tar.bz2"), ("es",)
    ),
    "fr-kroko": ModelSpec(
        "fr-kroko", "Französisch – Kroko", _url("sherpa-onnx-streaming-zipformer-fr-kroko-2025-08-06.tar.bz2"), ("fr",)
    ),
    "multi-8": ModelSpec(
        "multi-8",
        "Multilingual (ar/en/id/ja/ru/th/vi/zh)",
        _url("sherpa-onnx-streaming-zipformer-ar_en_id_ja_ru_th_vi_zh-2025-02-10.tar.bz2"),
        ("ar", "en", "id", "ja", "ru", "th", "vi", "zh"),
    ),
    "zh-en": ModelSpec(
        "zh-en", "Chinesisch+Englisch (small)", _url("sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16.tar.bz2"), ("zh", "en")
    ),
    "zh-int8": ModelSpec(
        "zh-int8", "Chinesisch (int8)", _url("sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2"), ("zh",)
    ),
    "zh-multi-int8": ModelSpec(
        "zh-multi-int8", "Chinesisch multi-zh-hans (int8)", _url("sherpa-onnx-streaming-zipformer-multi-zh-hans-int8-2023-12-13.tar.bz2"), ("zh",)
    ),
    "ru-int8": ModelSpec(
        "ru-int8", "Russisch – Vosk small (int8)", _url("sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16.tar.bz2"), ("ru",)
    ),
    "bn": ModelSpec(
        "bn", "Bengali – Vosk", _url("sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09.tar.bz2"), ("bn",), model_type="zipformer2"
    ),
    "ko": ModelSpec(
        "ko", "Koreanisch", _url("sherpa-onnx-streaming-zipformer-korean-2024-06-16.tar.bz2"), ("ko",)
    ),
    "custom": ModelSpec(
        "custom", "Eigene Modell-URL (siehe model_url)", "", ("de",)
    ),
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


def archive_stem(url: str) -> str:
    name = Path(url).name
    for suffix in (".tar.bz2", ".tar.gz", ".tgz", ".tar", ".zip"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def resolve_spec(
    model_key: str, model_url: str | None = None, model_type: str | None = None
) -> ModelSpec:
    """Preset aufloesen; ``model_url``/``model_type`` duerfen ueberschreiben."""
    spec = MODELS.get(model_key, MODELS["de"])
    if not model_url:
        if model_type is not None and model_type != spec.model_type:
            return ModelSpec(spec.key, spec.label, spec.url, spec.languages, model_type, spec.dir)
        return spec
    return ModelSpec(
        key=spec.key if model_key in MODELS else "custom",
        label=spec.label,
        url=model_url,
        languages=spec.languages,
        model_type=model_type if model_type is not None else spec.model_type,
        dir=archive_stem(model_url),
    )


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
    spec: ModelSpec,
    models_dir: str | Path | None = None,
    logger: LogFn = log,
) -> Path:
    if not spec.url:
        raise ValueError("Keine Modell-URL gesetzt")
    directory = Path(models_dir) if models_dir else default_models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (spec.dir or archive_stem(spec.url))
    if (target / "tokens.txt").exists():
        return target

    archive = directory / Path(spec.url).name
    logger(f"[MODEL] {spec.label} ({target.name})")
    if not archive.exists():
        _download(spec.url, archive, logger)
    logger(f"[MODEL] Entpacke {archive.name} ...")
    with tarfile.open(archive, "r:bz2") as tar:
        try:
            tar.extractall(directory, filter="data")
        except TypeError:
            tar.extractall(directory)
    if (target / "tokens.txt").exists():
        return target
    # Fallback: Ordner mit demselben Stem oder irgendein gueltiger Ordner
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
            enable_endpoint_detection=False,
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
            "model_type": self.model_type or "(auto)",
            "sherpa_version": getattr(sherpa_onnx, "__version__", "?"),
            "load_seconds": round(self.load_seconds, 3),
        }


def build_engine(
    model_key: str,
    model_url: str | None = None,
    num_threads: int = 2,
    model_type: str | None = None,
    languages: list[str] | None = None,
    logger: LogFn = log,
) -> STTEngine:
    spec = resolve_spec(model_key, model_url or None, model_type)
    model_dir = ensure_model(spec, logger=logger)
    return STTEngine(
        spec.key,
        model_dir,
        languages=languages or list(spec.languages),
        label=spec.label,
        num_threads=num_threads,
        model_type=spec.model_type,
        logger=logger,
    )
