"""sherpa-onnx STT fuer den Wyoming-Server.

Zwei Engine-Familien:

* **Streaming** (``OnlineRecognizer.from_transducer``): Zipformer-Transducer
  mit ``encoder``/``decoder``/``joiner``/``tokens``. Liefert Partials.
* **Offline** (``OfflineRecognizer``): Whisper (``from_whisper``) und
  NeMo Canary (``from_nemo_canary``). Dekodiert erst nach ``audio-stop``.

Beide stellen dieselbe Session-Schnittstelle bereit (``accept``/``result``/
``finalize``), damit der Wyoming-Handler identisch bleibt.
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

WHISPER_LANGS = (
    "de", "en", "es", "fr", "it", "nl", "pl", "pt", "ru", "tr", "uk", "cs",
    "sv", "da", "fi", "no", "hu", "ro", "el", "ar", "zh", "ja", "ko",
)


def log(message: str) -> None:
    print(message, flush=True)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    url: str
    languages: tuple[str, ...]
    kind: str = "streaming"  # streaming | whisper | canary
    model_type: str = ""
    dir: str = ""
    language: str = ""  # Standard-Quellsprache fuer Offline-Modelle


def _url(name: str) -> str:
    return _RELEASE + name


# Streaming-Zipformer (Transducer) + Offline-Modelle.
MODELS: dict[str, ModelSpec] = {
    # --- Streaming (Zipformer) ---
    "de": ModelSpec("de", "Deutsch – Kroko (Streaming, beste DE-Qualität)",
                    _url("sherpa-onnx-streaming-zipformer-de-kroko-2025-08-06.tar.bz2"), ("de",)),
    "en-kroko": ModelSpec("en-kroko", "English – Kroko (Streaming)",
                          _url("sherpa-onnx-streaming-zipformer-en-kroko-2025-08-06.tar.bz2"), ("en",)),
    "en-20M": ModelSpec("en-20M", "English – 20M (Streaming, klein)",
                        _url("sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2"), ("en",)),
    "es-kroko": ModelSpec("es-kroko", "Spanisch – Kroko (Streaming)",
                          _url("sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06.tar.bz2"), ("es",)),
    "fr-kroko": ModelSpec("fr-kroko", "Französisch – Kroko (Streaming)",
                          _url("sherpa-onnx-streaming-zipformer-fr-kroko-2025-08-06.tar.bz2"), ("fr",)),
    "multi-8": ModelSpec("multi-8", "Multilingual ar/en/id/ja/ru/th/vi/zh (Streaming)",
                         _url("sherpa-onnx-streaming-zipformer-ar_en_id_ja_ru_th_vi_zh-2025-02-10.tar.bz2"),
                         ("ar", "en", "id", "ja", "ru", "th", "vi", "zh")),
    "zh-en": ModelSpec("zh-en", "Chinesisch+Englisch (Streaming)",
                       _url("sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16.tar.bz2"), ("zh", "en")),
    "zh-int8": ModelSpec("zh-int8", "Chinesisch int8 (Streaming)",
                         _url("sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2"), ("zh",)),
    "zh-multi-int8": ModelSpec("zh-multi-int8", "Chinesisch multi-zh-hans int8 (Streaming)",
                               _url("sherpa-onnx-streaming-zipformer-multi-zh-hans-int8-2023-12-13.tar.bz2"), ("zh",)),
    "ru-int8": ModelSpec("ru-int8", "Russisch – Vosk small int8 (Streaming)",
                         _url("sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16.tar.bz2"), ("ru",)),
    "bn": ModelSpec("bn", "Bengali – Vosk (Streaming)",
                    _url("sherpa-onnx-streaming-zipformer-bn-vosk-2026-02-09.tar.bz2"), ("bn",), model_type="zipformer2"),
    "ko": ModelSpec("ko", "Koreanisch (Streaming)",
                    _url("sherpa-onnx-streaming-zipformer-korean-2024-06-16.tar.bz2"), ("ko",)),

    # --- Offline: Whisper ---
    "whisper-tiny-int8": ModelSpec("whisper-tiny-int8", "Whisper tiny int8 (Offline, schnell)",
                                   _url("sherpa-onnx-whisper-tiny.tar.bz2"), WHISPER_LANGS,
                                   kind="whisper", language="de"),
    "whisper-base-int8": ModelSpec("whisper-base-int8", "Whisper base int8 (Offline)",
                                   _url("sherpa-onnx-whisper-base.tar.bz2"), WHISPER_LANGS,
                                   kind="whisper", language="de"),
    "whisper-small-int8": ModelSpec("whisper-small-int8", "Whisper small int8 (Offline, gute DE-Qualität)",
                                    _url("sherpa-onnx-whisper-small.tar.bz2"), WHISPER_LANGS,
                                    kind="whisper", language="de"),

    # --- Offline: NeMo Canary ---
    "canary-180m-flash-int8": ModelSpec(
        "canary-180m-flash-int8", "NeMo Canary 180M flash int8 (Offline, en/es/de/fr)",
        _url("sherpa-onnx-nemo-canary-180m-flash-en-es-de-fr-int8.tar.bz2"),
        ("en", "es", "de", "fr"), kind="canary", language="de",
    ),

    # --- Eigene URL ---
    "custom": ModelSpec("custom", "Eigene Modell-URL (siehe model_url)", "", ("de",)),
}


@dataclass(frozen=True)
class ModelFiles:
    encoder: Path
    decoder: Path
    tokens: Path
    joiner: Path | None = None


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
    model_key: str,
    model_url: str | None = None,
    model_type: str | None = None,
    kind: str | None = None,
) -> ModelSpec:
    """Preset aufloesen; URL/Typ/Art duerfen ueberschreiben."""
    spec = MODELS.get(model_key, MODELS["de"])
    resolved_kind = kind or spec.kind
    resolved_type = model_type if model_type is not None else spec.model_type
    if not model_url:
        if resolved_kind == spec.kind and resolved_type == spec.model_type:
            return spec
        return ModelSpec(spec.key, spec.label, spec.url, spec.languages, resolved_kind, resolved_type, spec.dir, spec.language)
    return ModelSpec(
        key=spec.key if model_key in MODELS else "custom",
        label=spec.label,
        url=model_url,
        languages=spec.languages,
        kind=resolved_kind,
        model_type=resolved_type,
        dir=archive_stem(model_url),
        language=spec.language,
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
            if time.time() - last > 5:
                last = time.time()
                logger(
                    f"[MODEL]   {done / 1e6:.1f}/{total / 1e6:.1f} MB"
                    if total
                    else f"[MODEL]   {done / 1e6:.1f} MB"
                )
    tmp.rename(dest)


def ensure_model(spec: ModelSpec, models_dir: str | Path | None = None, logger: LogFn = log) -> Path:
    if not spec.url:
        raise ValueError("Keine Modell-URL gesetzt")
    directory = Path(models_dir) if models_dir else default_models_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (spec.dir or archive_stem(spec.url))
    if _looks_complete(target):
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
    if _looks_complete(target):
        return target
    for candidate in sorted(directory.iterdir()):
        if candidate.is_dir() and _looks_complete(candidate):
            return candidate
    raise RuntimeError(f"Modell konnte nicht entpackt werden: {target}")


def _looks_complete(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_tokens = bool(list(path.glob("*tokens.txt")))
    has_encoder = bool(list(path.glob("*encoder*.onnx")))
    return has_tokens and has_encoder


def _pick(model_dir: Path, pattern: str) -> Path:
    candidates = sorted(model_dir.glob(pattern))
    if not candidates:
        raise FileNotFoundError(f"Keine Datei {pattern} in {model_dir}")
    int8 = [c for c in candidates if ".int8." in c.name]
    non_int8 = [c for c in candidates if ".int8." not in c.name]
    return (int8 or non_int8 or candidates)[0]


def find_model_files(model_dir: str | Path, *, offline: bool = False) -> ModelFiles:
    model_dir = Path(model_dir)
    tokens = _pick(model_dir, "*tokens.txt")
    encoder = _pick(model_dir, "*encoder*.onnx")
    decoder = _pick(model_dir, "*decoder*.onnx")
    if offline:
        return ModelFiles(encoder=encoder, decoder=decoder, tokens=tokens)
    joiner = _pick(model_dir, "*joiner*.onnx")
    return ModelFiles(encoder=encoder, decoder=decoder, tokens=tokens, joiner=joiner)


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
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
    kind = "streaming"

    def __init__(self, model_key, model_dir, *, languages=None, label="", sample_rate=16000,
                 num_threads=2, provider="cpu", model_type="", logger: LogFn = log) -> None:
        self.model_key = model_key
        self.model_dir = Path(model_dir)
        self.label = label or model_key
        self.languages = languages or ["de"]
        self.sample_rate = sample_rate
        self.num_threads = num_threads
        self.provider = provider
        self.model_type = model_type
        self.language = ""
        self.lock = threading.RLock()

        files = find_model_files(self.model_dir)
        logger(f"[STT] {self.label}: encoder={files.encoder.name}")
        logger(f"[STT] threads={num_threads} provider={provider} model_type={model_type or '(auto)'}")
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
            "kind": self.kind, "model": self.model_key, "label": self.label,
            "languages": self.languages, "language": self.language,
            "model_dir": str(self.model_dir), "sample_rate": self.sample_rate,
            "num_threads": self.num_threads, "provider": self.provider,
            "model_type": self.model_type or "(auto)",
            "sherpa_version": getattr(sherpa_onnx, "__version__", "?"),
            "load_seconds": round(self.load_seconds, 3),
        }


# ---------------------------------------------------------------------------
# Offline (Whisper, Canary)
# ---------------------------------------------------------------------------
class OfflineSTTSession:
    def __init__(self, engine: "OfflineSTTEngine") -> None:
        self._engine = engine
        self._chunks: list[np.ndarray] = []

    def accept(self, samples: np.ndarray) -> None:
        self._chunks.append(np.asarray(samples, dtype=np.float32))

    def result(self) -> str:
        return ""  # Offline-Modelle liefern keine Partials

    def reset(self) -> None:
        self._chunks = []

    def finalize(self) -> str:
        audio = (
            np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        )
        self._chunks = []
        return self._engine.transcribe(audio)


class OfflineSTTEngine:
    def __init__(self, model_key, model_dir, *, kind, languages=None, label="", sample_rate=16000,
                 num_threads=2, provider="cpu", language="de", logger: LogFn = log) -> None:
        self.kind = kind
        self.model_key = model_key
        self.model_dir = Path(model_dir)
        self.label = label or model_key
        self.languages = languages or [language or "de"]
        self.sample_rate = sample_rate
        self.num_threads = num_threads
        self.provider = provider
        self.language = language or "de"
        self.model_type = kind
        self.lock = threading.RLock()

        files = find_model_files(self.model_dir, offline=True)
        logger(f"[STT] {self.label}: encoder={files.encoder.name} kind={kind} lang={self.language}")
        started = time.perf_counter()
        if kind == "whisper":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_whisper(
                encoder=str(files.encoder),
                decoder=str(files.decoder),
                tokens=str(files.tokens),
                language=self.language,
                task="transcribe",
                num_threads=num_threads,
                provider=provider,
            )
        elif kind == "canary":
            self.recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_canary(
                encoder=str(files.encoder),
                decoder=str(files.decoder),
                tokens=str(files.tokens),
                src_lang=self.language,
                tgt_lang=self.language,
                num_threads=num_threads,
                provider=provider,
            )
        else:
            raise ValueError(f"Unbekannte Offline-Art: {kind}")
        self.load_seconds = time.perf_counter() - started
        logger(f"[STT] Recognizer bereit in {self.load_seconds:.2f}s")

    def create_session(self) -> OfflineSTTSession:
        return OfflineSTTSession(self)

    def transcribe(self, samples: np.ndarray) -> str:
        with self.lock:
            stream = self.recognizer.create_stream()
            stream.accept_waveform(self.sample_rate, np.asarray(samples, dtype=np.float32))
            self.recognizer.decode_stream(stream)
            return stream.result.text

    def info(self) -> dict:
        return {
            "kind": self.kind, "model": self.model_key, "label": self.label,
            "languages": self.languages, "language": self.language,
            "model_dir": str(self.model_dir), "sample_rate": self.sample_rate,
            "num_threads": self.num_threads, "provider": self.provider,
            "model_type": self.kind,
            "sherpa_version": getattr(sherpa_onnx, "__version__", "?"),
            "load_seconds": round(self.load_seconds, 3),
        }


def build_engine(
    model_key: str,
    model_url: str | None = None,
    num_threads: int = 2,
    model_type: str | None = None,
    languages: list[str] | None = None,
    kind: str | None = None,
    logger: LogFn = log,
):
    spec = resolve_spec(model_key, model_url or None, model_type, kind)
    model_dir = ensure_model(spec, logger=logger)
    resolved_languages = languages or list(spec.languages)
    if spec.kind == "streaming":
        return STTEngine(
            spec.key, model_dir, languages=resolved_languages, label=spec.label,
            num_threads=num_threads, model_type=spec.model_type, logger=logger,
        )
    source_language = (
        languages[0] if languages else (spec.language or (resolved_languages[0] if resolved_languages else "de"))
    ) or "de"
    return OfflineSTTEngine(
        spec.key, model_dir, kind=spec.kind, languages=resolved_languages, label=spec.label,
        num_threads=num_threads, language=source_language, logger=logger,
    )
